"""Download chosen images to a local mirror, record in DB, rewrite markdown URLs."""
from __future__ import annotations

import hashlib
import html
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from loguru import logger

# Network errors worth retrying (transient: DNS / TCP / TLS / proxy hiccups).
# Permanent errors (403/404/ENOSPC/...) are NOT retried — they'd waste the
# same failures 3x and risk CDN rate-limit escalation.
#
# requests' exception tree does NOT subclass the built-in ConnectionError /
# TimeoutError (it goes RequestException -> OSError), so the requests.*
# types must be listed explicitly — otherwise a read timeout (the exact
# failure mode behind bkimg.cdn.bcebos.com dropping) bypasses the retry
# loop and fails on the first attempt.
import requests as _requests

_RETRIABLE: Tuple[type, ...] = (
    ConnectionError,
    TimeoutError,
    _requests.exceptions.Timeout,
    _requests.exceptions.ConnectionError,
)

# Hardcoded retry policy. 3 attempts, exponential backoff: 1.5s, 2.25s.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.5

# Display-size cap for images embedded in the final research Markdown.
# Images with max(width, height) > this threshold get a width/height
# attribute injected into the <img> tag so the renderer (GitHub /
# GitLab / most Markdown→HTML converters) shrinks them proportionally.
# 600 px matches the typical GitHub README content width.
_MAX_DISPLAY_PX = 600

# Undersize floor: images whose pixel AREA is below this are upscaled at
# persist time so both sides reach ``_MIN_DISPLAY_SIDE``, aspect preserved.
# 40,000 = 200² catches thumbnails/icons (150×200=30k upscaled) while
# leaving modestly small but readable images (250×200=50k) untouched.
_MIN_DISPLAY_AREA = 40_000
_MIN_DISPLAY_SIDE = 300

# Wikimedia serves thumbnails at an arbitrary width encoded in the last
# path segment (``/250px-NAME.jpg``). Wikipedia embeds 250px renditions,
# which land near 250x170 — just above the _MIN_DISPLAY_AREA upscale
# floor, so they were kept at 250px despite a short side well under
# _MIN_DISPLAY_SIDE. Requesting a wider rendition yields real pixels
# instead of interpolation. Widths above the source return HTTP 400, so
# the caller must be able to fall back to the original URL.
_WIKIMEDIA_THUMB_HOSTS = ("upload.wikimedia.org",)
_WIKIMEDIA_THUMB_RE = re.compile(r"^(\d+)px-")


def _wikimedia_larger_url(url: str) -> Optional[str]:
    """Return the same Wikimedia thumbnail at _MAX_DISPLAY_PX wide.

    ``None`` when the URL is not a Wikimedia thumbnail or is already at
    least as wide as the display cap (nothing to gain).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(
        host == h or host.endswith("." + h) for h in _WIKIMEDIA_THUMB_HOSTS
    ):
        return None
    head, sep, name = parsed.path.rpartition("/")
    if not sep:
        return None
    match = _WIKIMEDIA_THUMB_RE.match(name)
    if not match or int(match.group(1)) >= _MAX_DISPLAY_PX:
        return None
    new_name = _WIKIMEDIA_THUMB_RE.sub(f"{_MAX_DISPLAY_PX}px-", name, count=1)
    return urlunparse(parsed._replace(path=f"{head}/{new_name}"))


def _wikimedia_original_url(url: str) -> Optional[str]:
    """Return the original file behind a Wikimedia thumbnail URL.

    ``.../commons/thumb/4/41/NAME.jpg/250px-NAME.jpg``
    → ``.../commons/4/41/NAME.jpg``
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(
        host == h or host.endswith("." + h) for h in _WIKIMEDIA_THUMB_HOSTS
    ):
        return None
    parts = parsed.path.split("/")
    if "thumb" not in parts or len(parts) < 3:
        return None
    parts.remove("thumb")
    # The rendition segment (``250px-NAME.jpg``) is dropped; what remains
    # ends with the source filename.
    return urlunparse(parsed._replace(path="/".join(parts[:-1]), query=""))


def _wikimedia_candidates(url: str) -> List[str]:
    """Higher-resolution renditions to try, best-bounded first.

    Wikipedia embeds 250px thumbnails whose short side falls well under
    ``_MIN_DISPLAY_SIDE``. Rather than interpolate them up, ask the CDN
    for real pixels:

    1. the ``_MAX_DISPLAY_PX``-wide thumbnail — bounded in size, and all
       that is needed whenever the source is at least that wide;
    2. the original file — only reached when step 1 returns HTTP 400,
       which itself proves the source is narrower than
       ``_MAX_DISPLAY_PX``, so this download is small by construction.

    Empty for non-Wikimedia URLs, so other hosts cost no extra request.
    """
    candidates = []
    for candidate in (_wikimedia_larger_url(url), _wikimedia_original_url(url)):
        if candidate and candidate != url:
            candidates.append(candidate)
    return candidates

# Suffix-based hostname allowlist for image persistence. Each suffix
# is matched against the URL's hostname (exact or ".suffix" sub-match)
# so that public CDNs whose IP-block check returns False for unrelated
# reasons (e.g. corporate proxy resolves via a private range inside
# the container) can still be reached. The resolved-IP check still
# runs inside validate_url(), so loopback / RFC1918 / CGNAT protection
# is unchanged — only the literal-IP literal-string check is bypassed
# for matching suffixes.
#
# Add a suffix here only after observing a real "URL failed security
# validation" log line for an obviously public CDN URL. Do NOT add
# generic CDNs speculatively.
_IMAGE_URL_TRUSTED_HOST_SUFFIXES: Tuple[str, ...] = (
    "cdninstagram.com",  # Instagram image CDN (scontent-hkg*.cdninstagram.com)
    "fbcdn.net",         # Facebook image CDN (scontent-*.fbcdn.net)
    "ctrip.com",         # Ctrip / Trip.com group (dimg*.c-ctrip.com)
    "digitaloceanspaces.com",  # self-hosted CDN used by fsholidays.my etc.
)

# The URL part allows balanced ``(...)`` pairs so filenames like
# ``szdd (41).jpg`` match in full. A plain ``[^)]+`` stops at the first
# ``)`` inside the filename, which truncates the captured URL (the
# download then 404s) and leaves a stranded ``.jpg)`` in the report when
# the image is dropped.
_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://(?:[^()]|\([^()]*\))+)\)")


def _probe_size(data: bytes, url: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Return (width, height) in pixels by opening bytes with PIL, or None.

    Failure modes (corrupt bytes, non-image MIME, decoder miss) return
    None — caller treats this as "unknown size" and skips resizing.
    """
    try:
        from io import BytesIO
        from PIL import Image as PILImage
        with PILImage.open(BytesIO(data)) as im:
            return im.size  # (width, height)
    except Exception as e:
        logger.debug(
            f"[IMG-TRACE] PROBE_SIZE_FAIL url={url or '<unknown>'} "
            f"reason={type(e).__name__}: {e}"
        )
        return None


def _probe_and_resize(
    data: bytes, url: Optional[str] = None
) -> Tuple[bytes, Optional[Tuple[int, int]], bool]:
    """Probe image dimensions and resize if the long side exceeds
    ``_MAX_DISPLAY_PX``.

    Returns ``(saved_bytes, size, resized)``:
    - ``saved_bytes``: original bytes (under cap / decode failure) or
      JPEG q85 bytes of the resized image.
    - ``size``: (w, h) of the returned bytes, or ``None`` if PIL could
      not decode (caller writes original bytes, size unknown).
    - ``resized``: True iff the bytes were re-encoded to JPEG at a
      smaller size. Caller uses this to override content-type/ext so
      the file extension matches the actual byte format.

    Resizing happens HERE (at persist time, before the bytes hit disk)
    so the saved file IS the reduced image and ``url_to_size`` reflects
    the saved dimensions. Previously the cap was half-disabled: RESIZE
    events were logged but original bytes were written, so oversized
    images rendered at native size (PDF export has no CSS max-width).
    """
    try:
        from io import BytesIO
        from PIL import Image as PILImage
        with PILImage.open(BytesIO(data)) as im:
            w, h = im.size
            long_side = max(w, h)
            if long_side > _MAX_DISPLAY_PX:
                # Oversized: downscale long side to _MAX_DISPLAY_PX.
                scale = _MAX_DISPLAY_PX / long_side
                new_size = (round(w * scale), round(h * scale))
                reason = "downscale"
            elif w * h < _MIN_DISPLAY_AREA:
                # Undersized: upscale so BOTH sides reach
                # _MIN_DISPLAY_SIDE, aspect preserved. Scale by the
                # smaller side so the floor is met exactly there and
                # the larger side exceeds it.
                scale = _MIN_DISPLAY_SIDE / min(w, h)
                new_size = (round(w * scale), round(h * scale))
                reason = "upscale"
            else:
                return data, (w, h), False
            im_resized = im.convert("RGB").resize(
                new_size, PILImage.LANCZOS
            )
            buf = BytesIO()
            im_resized.save(buf, format="JPEG", quality=85)
            resized_bytes = buf.getvalue()
            logger.info(
                f"[IMG-TRACE] PERSIST_RESIZE url={url or '<unknown>'} "
                f"from={w}x{h} to={new_size[0]}x{new_size[1]} "
                f"reason={reason} "
                f"max_px={_MAX_DISPLAY_PX} "
                f"min_area={_MIN_DISPLAY_AREA} min_side={_MIN_DISPLAY_SIDE}"
            )
            return resized_bytes, new_size, True
    except Exception as e:
        logger.debug(
            f"[IMG-TRACE] PROBE_SIZE_FAIL url={url or '<unknown>'} "
            f"reason={type(e).__name__}: {e}"
        )
    return data, None, False


class ImageStore:
    def __init__(
        self,
        research_id: str,
        db_session,
        base_dir: Path = Path("/data/images"),
        firecrawl_client=None,
    ) -> None:
        # Original id is stored in the DB (FK -> research_history.id, a UUID).
        self.research_id = research_id
        # Sanitized id is used ONLY for the filesystem path + served route,
        # so a malicious/odd id can never escape base_dir.
        self._safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", research_id)
        self.db_session = db_session
        self.base_dir = Path(base_dir)
        # Optional Firecrawl fallback client (self-hosted). When set and
        # safe_get fails with HTTP 4xx (anti-hotlink), _download re-fetches
        # the source page via Firecrawl (which renders with a real browser
        # and proper Referer) and re-extracts the image src from the
        # rendered HTML. See _download_via_firecrawl.
        self._firecrawl_client = firecrawl_client

    # Browser-like User-Agent. Many CDNs (and basic anti-hotlink setups)
    # reject the bare python-requests UA outright. A current Chrome UA
    # almost always passes the same checks real browsers do.
    _USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    def _download(
        self, url: str, source_url: Optional[str] = None
    ) -> Optional[Tuple[bytes, str]]:
        from ..security.safe_requests import safe_get
        from urllib.parse import urlparse

        # Many CDNs gate images behind basic anti-hotlink checks that only
        # verify the Referer header points back to the page hosting the
        # image. Setting it to the source page's origin (scheme + host,
        # no path) is the lowest-leak Referer that still passes the
        # common case — many sites reject empty Referer outright.
        headers = {"User-Agent": self._USER_AGENT}
        if source_url:
            parsed = urlparse(source_url)
            if parsed.scheme and parsed.netloc:
                headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        try:
            resp = safe_get(
                url,
                headers=headers,
                timeout=30,
                allow_private_ips=False,
                trusted_host_suffixes=_IMAGE_URL_TRUSTED_HOST_SUFFIXES,
            )
        except Exception as e:
            # Network-level failures (DNS / TCP / TLS / proxy hiccups).
            # Bubble up so persist()'s retry loop can decide whether to
            # back off — don't auto-firecrawl on flaky-network errors.
            logger.warning(
                f"[IMG-TRACE] PERSIST_DOWNLOAD_FAIL url={url} "
                f"reason={type(e).__name__}: {e}"
            )
            raise

        # Status-code based anti-hotlink detection. raise_for_status()
        # raises HTTPError on 4xx; we catch here to decide whether to
        # escalate to Firecrawl before propagating. 404/410 mean the
        # resource is genuinely gone — no fallback will help.
        if resp.status_code in (401, 403, 407):
            if (
                self._firecrawl_client is not None
                and source_url
            ):
                logger.info(
                    f"[IMG-TRACE] PERSIST_FALLBACK url={url} via=firecrawl "
                    f"reason=HTTP {resp.status_code} source={source_url}"
                )
                try:
                    return self._download_via_firecrawl(url, source_url)
                except Exception as fb_e:
                    logger.warning(
                        f"[IMG-TRACE] PERSIST_FALLBACK_FAIL url={url} "
                        f"reason={type(fb_e).__name__}: {fb_e}"
                    )
            # No fallback available (or it failed) — surface a clean error
            # for persist()'s PERSIST_FAIL log.
            from requests import HTTPError

            raise HTTPError(
                f"HTTP {resp.status_code}", response=resp
            )

        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return resp.content, ctype

    def _download_via_firecrawl(
        self, url: str, source_url: str
    ) -> Optional[Tuple[bytes, str]]:
        """Heavy fallback via self-hosted Firecrawl.

        Strategy: re-fetch the source page through Firecrawl (which renders
        in a real browser with full Referer / cookies), then locate the
        image by basename match against the rendered HTML's <img> tags.
        The resolved src URL is then downloaded via safe_get with the
        same Referer/UA headers — but the URL Firecrawl extracted is one
        the source page actually trusts, so anti-hotlink usually passes.

        Match key is the URL path's basename (no query string) because
        anti-hotlink signed URLs add query params that don't appear in
        the rendered <img src>.
        """
        from ..security.safe_requests import safe_get
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin, urlparse

        result = self._firecrawl_client.scrape(source_url, include_html=True)
        if not isinstance(result, dict):
            raise RuntimeError("firecrawl scrape returned no dict")
        html = result.get("html")
        if not isinstance(html, str) or not html:
            raise RuntimeError("firecrawl scrape returned no html")

        soup = BeautifulSoup(html, "html.parser")
        target_basename = urlparse(url).path.rsplit("/", 1)[-1].lower()
        matched_src: Optional[str] = None
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if not src or src.startswith("data:"):
                continue
            absolute = urljoin(source_url, src)
            basename = urlparse(absolute).path.rsplit("/", 1)[-1].lower()
            if basename and basename == target_basename:
                matched_src = absolute
                break

        if not matched_src:
            raise RuntimeError(
                f"firecrawl source page does not contain img with basename "
                f"{target_basename!r}"
            )

        # Re-download through safe_get with the same Referer/UA treatment
        # as the fast path — the matched URL should be one the source
        # page's CDN allows.
        parsed_source = urlparse(source_url)
        headers = {"User-Agent": self._USER_AGENT}
        if parsed_source.scheme and parsed_source.netloc:
            headers["Referer"] = (
                f"{parsed_source.scheme}://{parsed_source.netloc}/"
            )
        resp = safe_get(
            matched_src,
            headers=headers,
            timeout=30,
            allow_private_ips=False,
            trusted_host_suffixes=_IMAGE_URL_TRUSTED_HOST_SUFFIXES,
        )
        resp.raise_for_status()
        ctype = (
            resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            or "image/jpeg"
        )
        return resp.content, ctype

    @staticmethod
    def _ext_for(content_type: str) -> str:
        mapping = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        return mapping.get(content_type, ".bin")

    def persist(
        self,
        urls: List[str],
        url_to_alt: Optional[Dict[str, str]] = None,
        url_to_source: Optional[Dict[str, tuple]] = None,
    ) -> Dict[str, str]:
        url_to_route: Dict[str, str] = {}
        url_to_size: Dict[str, Tuple[int, int]] = {}
        url_to_alt = url_to_alt or {}
        url_to_source = url_to_source or {}
        for url in urls:
            try:
                result = None
                last_exc: Optional[BaseException] = None
                src = url_to_source.get(url)
                source_url = (src or (None, None))[0]
                # Prefer a wider Wikimedia rendition: real pixels beat
                # interpolating the 250px thumbnail up. Single best-effort
                # attempt — the CDN answers 400 when the requested width
                # exceeds the source, and any failure simply leaves the
                # original URL to the retry loop below. url_to_route /
                # url_to_size stay keyed by the original URL, which is what
                # the report markdown references.
                for candidate in _wikimedia_candidates(url):
                    try:
                        result = self._download(
                            candidate, source_url=source_url
                        )
                        logger.info(
                            f"[IMG-TRACE] HIRES_OK research={self.research_id} "
                            f"img_url={url} hi_res_url={candidate}"
                        )
                        break
                    except Exception as e:
                        logger.info(
                            f"[IMG-TRACE] HIRES_FALLBACK "
                            f"research={self.research_id} "
                            f"img_url={url} hi_res_url={candidate} "
                            f"reason={type(e).__name__}: {e}"
                        )
                        result = None
                if result is None:
                    for attempt in range(1, _MAX_ATTEMPTS + 1):
                        try:
                            result = self._download(
                                url, source_url=source_url
                            )
                            break
                        except _RETRIABLE as e:
                            last_exc = e
                            if attempt == _MAX_ATTEMPTS:
                                raise
                            sleep_s = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                            logger.info(
                                f"[IMG-TRACE] PERSIST_RETRY url={url} "
                                f"attempt={attempt}/{_MAX_ATTEMPTS} "
                                f"reason={type(e).__name__}: {e} "
                                f"sleep={sleep_s:.1f}s"
                            )
                            time.sleep(sleep_s)
                if result is None:
                    if last_exc is not None:
                        raise last_exc
                    continue
                data, ctype = result
                # Probe dimensions and resize if oversized (long side >
                # _MAX_DISPLAY_PX). PIL is opened exactly once here; the
                # returned bytes are what get written to disk, so the
                # saved file IS the reduced image and url_to_size holds
                # the saved (post-resize) dimensions. PIL decode failure
                # → original bytes written, size unknown. A resize also
                # re-encodes to JPEG, so override ctype/ext to match.
                data, size, resized = _probe_and_resize(data, url=url)
                if resized:
                    ctype = "image/jpeg"
                digest = hashlib.sha1(data).hexdigest()
                ext = self._ext_for(ctype)
                rel = f"{self._safe_id}/{digest}{ext}"
                local_path = self.base_dir / self._safe_id / f"{digest}{ext}"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                route = f"/images/{rel}"
                logger.info(
                    f"[IMG-TRACE] PERSIST_OK research={self.research_id} "
                    f"img_url={url} local_path={local_path} "
                    f"sha1={digest} route={route} "
                    f"ext={ext} bytes={len(data)}"
                )
                if size is not None:
                    url_to_size[url] = size
                src = url_to_source.get(url)
                self._record(
                    url,
                    str(local_path),
                    route,
                    digest,
                    alt=url_to_alt.get(url),
                    source_url=(src or (None, None))[0],
                    source_title=(src or (None, None))[1],
                    width=size[0] if size else None,
                    height=size[1] if size else None,
                )
                url_to_route[url] = route
            except Exception as e:
                logger.warning(
                    f"[IMG-TRACE] PERSIST_FAIL url={url} "
                    f"reason={type(e).__name__}: {e}"
                )
        # Stash size map on the instance for rewrite_markdown to consume
        # without changing the public return signature.
        self._last_url_to_size = url_to_size
        return url_to_route

    def _record(
        self, url, local_path, route, digest,
        alt=None, source_url=None, source_title=None,
        width=None, height=None,
    ) -> None:
        try:
            from ..database.models import Image

            self.db_session.add(
                Image(
                    research_id=self.research_id,
                    original_url=url,
                    local_path=local_path,
                    local_route=route,
                    alt=alt,
                    source_url=source_url,
                    source_title=source_title,
                    content_hash=digest,
                    width=width,
                    height=height,
                )
            )
            self.db_session.commit()
        except Exception as e:
            logger.warning(
                f"[IMG-TRACE] PERSIST_RECORD_FAIL url={url} "
                f"reason={type(e).__name__}: {e}"
            )
            self.db_session.rollback()

    def rewrite_markdown(
        self,
        markdown: str,
        url_to_route: Dict[str, str],
        url_to_size: Optional[Dict[str, Tuple[int, int]]] = None,
        url_to_source: Optional[Dict[str, tuple]] = None,
    ) -> str:
        """Replace remote image URLs with local routes.

        Every persisted image becomes a ``<figure class="ldr-img">``
        block containing an ``<img>`` and a ``<figcaption>`` showing the
        alt text as a caption. The figure renders in both WebUI and
        WeasyPrint PDF export (see ``.ldr-img`` rules in styles.css).
        When the post-resize size is known, the ``<img>`` carries
        ``width``/``height`` attributes for stable layout; when the size
        is unknown (PIL probe failed) those attributes are omitted.

        Images whose URL has no local route (download failed all
        retries) are dropped entirely — returning ``""`` removes the
        whole ``![alt](url)`` match so no remote URL leaks into the
        final report.
        """
        sizes = url_to_size if url_to_size is not None else getattr(
            self, "_last_url_to_size", {}
        )
        url_to_source = url_to_source or {}
        resized = under = unknown = dropped = 0

        def repl(m: re.Match) -> str:
            nonlocal resized, under, unknown, dropped
            alt, url = m.group(1), m.group(2)
            route = url_to_route.get(url)
            # Pull the (img_source_url, source_title) for this image so
            # the rewrite-stage events carry the same five-key schema
            # as the rest of the IMG-TRACE pipeline. ``img_source_url``
            # and ``ref_url`` are the same page by construction (the
            # image lives on the page that the report cites as the
            # reference) but we spell both out for grep-ability.
            src_entry = url_to_source.get(url) or (None, None)
            img_source_url = src_entry[0] or ""
            ref_url = img_source_url
            size = sizes.get(url)
            size_str = (
                f"{size[0]}x{size[1]}" if size is not None else "unknown"
            )
            if route is None:
                # No local route means download failed all retries. Drop the
                # image entirely so no remote URL / broken <img> leaks into
                # the final report (remote URLs expire, get anti-hotlinked,
                # and expose the scrape). Returning "" removes the whole
                # ![alt](url) match.
                dropped += 1
                logger.info(
                    f"[IMG-TRACE] REWRITE_DROP research={self.research_id} "
                    f"img_alt={(alt or '')!r} "
                    f"img_url={url} "
                    f"img_source_url={img_source_url} "
                    f"cite_num=- ref_url={ref_url} "
                    f"reason=no_local_route"
                )
                return ""
            # Determine size + emit the appropriate KEEP/RESIZE event.
            if size is None:
                unknown += 1
                logger.info(
                    f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                    f"img_alt={(alt or '')!r} "
                    f"img_url={url} "
                    f"img_source_url={img_source_url} "
                    f"cite_num=- ref_url={ref_url} "
                    f"route={route} size=unknown"
                )
                size_attrs = ""
            else:
                w, h = size
                long_side = max(w, h)
                if long_side <= _MAX_DISPLAY_PX:
                    under += 1
                    logger.info(
                        f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                        f"img_alt={(alt or '')!r} "
                        f"img_url={url} "
                        f"img_source_url={img_source_url} "
                        f"cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h}"
                    )
                else:
                    resized += 1
                    logger.info(
                        f"[IMG-TRACE] RESIZE research={self.research_id} "
                        f"img_alt={(alt or '')!r} "
                        f"img_url={url} "
                        f"img_source_url={img_source_url} "
                        f"cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h} max_px={_MAX_DISPLAY_PX}"
                    )
                    logger.info(
                        f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                        f"img_alt={(alt or '')!r} "
                        f"img_url={url} "
                        f"img_source_url={img_source_url} "
                        f"cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h} max_px={_MAX_DISPLAY_PX}"
                    )
                size_attrs = f' width="{w}" height="{h}"'
            # Unified HTML figure output (WebUI + WeasyPrint PDF). Every
            # persisted image becomes <figure class="ldr-img"> with the
            # alt text shown as a <figcaption> caption. ``safe_alt`` is
            # reused for the alt attribute AND the caption text so they
            # never diverge; html.escape(quote=True) covers <, >, &, ".
            safe_alt = html.escape(alt, quote=True)
            return (
                f'<figure class="ldr-img">'
                f'<img src="{route}" alt="{safe_alt}"{size_attrs} loading="lazy" />'
                f'<figcaption>{safe_alt}</figcaption>'
                f'</figure>'
            )

        result = _IMG_RE.sub(repl, markdown)
        logger.info(
            f"[IMG-TRACE] RESIZE chosen={len(url_to_route)} "
            f"resized={resized} under_threshold={under} unknown_size={unknown} "
            f"dropped_unpersisted={dropped} max_px={_MAX_DISPLAY_PX}"
        )
        return result
