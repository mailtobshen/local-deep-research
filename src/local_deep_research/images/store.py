"""Download chosen images to a local mirror, record in DB, rewrite markdown URLs."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

# Network errors worth retrying (transient: DNS / TCP / TLS / proxy hiccups).
# Permanent errors (403/404/ENOSPC/...) are NOT retried — they'd waste the
# same failures 3x and risk CDN rate-limit escalation.
_RETRIABLE: Tuple[type, ...] = (ConnectionError, TimeoutError)

# Hardcoded retry policy. 3 attempts, exponential backoff: 1.5s, 2.25s.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.5

# Display-size cap for images embedded in the final research Markdown.
# Images with max(width, height) > this threshold get a width/height
# attribute injected into the <img> tag so the renderer (GitHub /
# GitLab / most Markdown→HTML converters) shrinks them proportionally.
# 600 px matches the typical GitHub README content width.
_MAX_DISPLAY_PX = 600

_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")


def _probe_size(data: bytes) -> Optional[Tuple[int, int]]:
    """Return (width, height) in pixels by opening bytes with PIL, or None.

    Failure modes (corrupt bytes, non-image MIME, decoder miss) return
    None — caller treats this as "unknown size" and skips resizing.
    """
    try:
        from io import BytesIO
        from PIL import Image as PILImage
        with PILImage.open(BytesIO(data)) as im:
            return im.size  # (width, height)
    except Exception:
        return None


class ImageStore:
    def __init__(
        self, research_id: str, db_session, base_dir: Path = Path("/data/images")
    ) -> None:
        # Original id is stored in the DB (FK -> research_history.id, a UUID).
        self.research_id = research_id
        # Sanitized id is used ONLY for the filesystem path + served route,
        # so a malicious/odd id can never escape base_dir.
        self._safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", research_id)
        self.db_session = db_session
        self.base_dir = Path(base_dir)

    def _download(self, url: str) -> Optional[Tuple[bytes, str]]:
        from ..security.safe_requests import safe_get

        resp = safe_get(url, timeout=30, allow_private_ips=False)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
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
                for attempt in range(1, _MAX_ATTEMPTS + 1):
                    try:
                        result = self._download(url)
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
                digest = hashlib.sha1(data).hexdigest()
                ext = self._ext_for(ctype)
                rel = f"{self._safe_id}/{digest}{ext}"
                local_path = self.base_dir / self._safe_id / f"{digest}{ext}"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                route = f"/images/{rel}"
                # Probe real dimensions for downstream display-size capping.
                # PIL may fail (corrupt bytes, non-image MIME) → skip resize.
                size = _probe_size(data)
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
        except Exception:
            logger.debug(f"Image DB record failed for {url}")
            self.db_session.rollback()

    def rewrite_markdown(
        self,
        markdown: str,
        url_to_route: Dict[str, str],
        url_to_size: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> str:
        """Replace remote image URLs with local routes; cap display size.

        If url_to_size is given (or stashed from persist()), any image
        whose max(width, height) exceeds _MAX_DISPLAY_PX gets a width or
        height attribute injected into the <img> tag so the renderer
        shrinks it proportionally (preserving aspect ratio).

        Below the threshold → no attribute injected, original size shown.
        Unknown size (PIL probe failed) → no attribute injected.
        """
        sizes = url_to_size if url_to_size is not None else getattr(
            self, "_last_url_to_size", {}
        )
        resized = under = unknown = 0

        def repl(m: re.Match) -> str:
            nonlocal resized, under, unknown
            alt, url = m.group(1), m.group(2)
            route = url_to_route.get(url)
            if route is None:
                return m.group(0)
            size = sizes.get(url)
            if size is None:
                unknown += 1
                return f"![{alt}]({route})"
            w, h = size
            long_side = max(w, h)
            if long_side <= _MAX_DISPLAY_PX:
                under += 1
                return f"![{alt}]({route})"
            resized += 1
            # Cap the LONG side only (width for landscape, height for portrait)
            # so the renderer keeps aspect ratio.
            if w >= h:
                return f"![{alt}]({route}){{width={_MAX_DISPLAY_PX}}}"
            return f"![{alt}]({route}){{height={_MAX_DISPLAY_PX}}}"

        result = _IMG_RE.sub(repl, markdown)
        logger.info(
            f"[IMG-TRACE] RESIZE chosen={len(url_to_route)} "
            f"resized={resized} under_threshold={under} unknown_size={unknown} "
            f"max_px={_MAX_DISPLAY_PX}"
        )
        return result
