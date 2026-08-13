"""Extract real <img> from scraped HTML into a normalized list."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

# URL substrings that almost always indicate non-content images.
_BLACKLIST_KEYWORDS = (
    "logo",
    "icon",
    "avatar",
    "sprite",
    "pixel",
    "tracker",
    "blank.gif",
)
_MIN_DIM = 50  # px; anything smaller is treated as an icon/pixel

# Default CSS selectors tried in order to find the page's main content
# area. Tuned to cover the most common blog / news / wiki structures;
# the first match wins. If none match, extraction falls back to the
# whole document so that odd pages still yield something.
_DEFAULT_CONTENT_ROOTS: Sequence[str] = (
    "article",
    "main",
    '[role="main"]',
    ".article-content",
    ".post-content",
    ".entry-content",
    "#content",
    "#main",
)


@dataclass
class ExtractedImage:
    url: str
    alt: str
    source_url: str
    source_title: str
    width: Optional[int]
    height: Optional[int]


def _to_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip().rstrip("px"))
    except (ValueError, TypeError):
        return None


def _is_blacklisted(url: str) -> bool:
    low = url.lower()
    return any(kw in low for kw in _BLACKLIST_KEYWORDS)


def _resolve_scope(soup: BeautifulSoup, roots: Sequence[str]):
    """Return the first matching subtree, or the whole soup if none match.

    A subtree that contains NO <img> tags does not count as a match — the
    next selector is tried. This avoids picking an empty <main> wrapper
    over a richer <article> sibling.
    """
    for sel in roots:
        node = soup.select_one(sel)
        if node is None:
            continue
        if node.find("img") is not None:
            return node
    return soup


# A "word" run is a stretch of >=2 chars that contains at least one letter.
# Used to tell a real named entity (Jane_Doe, Steven_Spielberg) from a
# non-descriptive token (x, img, 1, ABC123). Pure codes/numbers do not count.
_WORD_RUN = re.compile(r"[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*")
# A leading alphanumeric code to strip, e.g. "MKr25402_" in
# "MKr25402_Steven_Spielberg_(...).jpg" — a run with digits that is NOT a
# dictionary word (has a digit, or is all-caps short).
_LEADING_CODE = re.compile(r"^[A-Za-z0-9]*\d[A-Za-z0-9]*[_\-]+")


def _alt_from_filename(url: str) -> str:
    """Derive a human-readable alt from an image URL's filename.

    Only filenames that carry a recognizable named entity yield a non-empty
    alt; generic/non-descriptive filenames (x.jpg, img.jpg, 1.png) return ""
    so we never synthesize a meaningless alt. Pipeline:

      1. Take the URL path's last segment, drop the query string.
      2. Percent-decode (%28->( , %29->) , %20->space).
      3. Strip the file extension.
      4. Drop a leading alphanumeric code (e.g. MKr25402_) if present.
      5. Drop parenthesized clauses (e.g. "(Berlinale 2023)").
      6. Split on _ / - and keep only "word" runs (those with a letter);
         collapse to a single space.
      7. If fewer than two word runs survive AND the single run is a known
         generic token, return "" — otherwise return the joined text.
    """
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if not name:
        return ""
    name = unquote(name)
    # Strip extension.
    name = name.rsplit(".", 1)[0] if "." in name else name
    # Drop leading code prefixes like "MKr25402_" or "250px-". Wikimedia
    # thumbnail paths stack them ("250px-MKr25402_Steven_..."), so repeat
    # until no more leading-code segment remains.
    while True:
        stripped = _LEADING_CODE.sub("", name)
        if stripped == name:
            break
        name = stripped
    # Drop parenthesized clauses: "Steven_Spielberg_(Berlinale_2023)" -> "Steven_Spielberg_"
    name = re.sub(r"\([^)]*\)", " ", name)
    # Split on separators and keep word runs.
    tokens = [t for t in re.split(r"[_\-]+", name) if t]
    words = [t for t in tokens if _WORD_RUN.fullmatch(t) and re.search(r"[A-Za-z]", t)]
    if not words:
        return ""
    # Reject generic/non-descriptive single tokens: a known generic word,
    # or any single token <= 2 chars (e.g. x, a, 1) too ambiguous to be a
    # meaningful alt on its own.
    generic = {"img", "image", "images", "photo", "file", "pic", "picture"}
    if len(words) == 1 and (words[0].lower() in generic or len(words[0]) <= 2):
        return ""
    # Reject content-hash tokens (Baidu BOS / CDN hashes): a long token
    # whose chars are mostly [0-9a-f] is a hex digest, not a name.
    words = [w for w in words if not _looks_like_hash(w)]
    if not words:
        return ""
    return " ".join(words)


_HEX = set("0123456789abcdef")


def _looks_like_hash(token: str) -> bool:
    """True for bare content-hash filenames (Baidu BOS, CDN digests).

    A token of >=12 chars whose lowercase chars are >=80% hex digits is
    treated as a hash. Real words rarely run that long without vowels /
    separators, and even hex-ish names like "deadbeef" are short enough
    to be ambiguous — the length floor avoids false positives there.
    """
    s = token.lower()
    if len(s) < 12:
        return False
    hex_frac = sum(1 for c in s if c in _HEX) / len(s)
    return hex_frac >= 0.8


def _resolve_alt(
    img, absolute_url: str, source_url: str = ""
) -> tuple[str, Optional[str], Optional[str]]:
    """Return (alt_text, via, from_label) for an <img>, with fallbacks.

    Order: explicit alt → sibling <figcaption> (inside the nearest <figure>
    ancestor, Wikipedia-scoped) → Baidu Baike structural caption (Baike-
    scoped) → named entity parsed from the URL filename.

    ``via`` is None when an explicit alt was present (not a fallback) or
    when nothing yielded a non-empty value; otherwise it names the
    fallback that won: "figcaption" | "baike" | "filename". ``from_label``
    is the sub-source (figcaption | titlespan | a_title | json | filename),
    used by the ALT_RESOLVE probe. The alt_text itself is "" when nothing
    yielded a value (preserves the empty-alt contract).
    """
    alt = (img.get("alt") or "").strip()
    if alt:
        return (alt, None, None)
    # Fallback 1: <figcaption> inside the enclosing <figure>, scoped to
    # Wikipedia/Wikimedia (the structure we tuned this for). Off-wiki, a
    # <figcaption> is not assumed to be a usable alt.
    if _is_wiki(source_url, absolute_url):
        figure = img.find_parent("figure")
        if figure is not None:
            cap = figure.find("figcaption")
            if cap is not None:
                cap_text = cap.get_text(" ", strip=True)
                if cap_text:
                    return (cap_text, "figcaption", "figcaption")
    # Fallback 2: Baidu Baike lemma-picture caption (scoped to Baike).
    baike_text, baike_from = _baike_alt(img, source_url, absolute_url)
    if baike_text:
        return (baike_text, "baike", baike_from)
    # Fallback 3: named entity in the URL filename.
    fn = _alt_from_filename(absolute_url)
    if fn:
        return (fn, "filename", "filename")
    return ("", None, None)


_BAIKE_HOSTS = ("baike.baidu.com",)
# Baidu BOS image CDN hosts the actual <img src>; used as a secondary
# signal that the page is Baike even if source_url parsing is off.
_BAIKE_IMG_HOSTS = ("bkimg.cdn.bcebos.com",)

# Wikipedia/Wikimedia scope for the <figure>/<figcaption> alt fallback.
_WIKI_HOSTS = ("wikipedia.org",)
_WIKI_IMG_HOSTS = ("upload.wikimedia.org",)


def _is_baike(source_url: str, img_url: str) -> bool:
    host_src = (urlparse(source_url).hostname or "").lower()
    host_img = (urlparse(img_url).hostname or "").lower()
    return (
        any(h in host_src for h in _BAIKE_HOSTS)
        or any(h in host_img for h in _BAIKE_IMG_HOSTS)
    )


def _is_wiki(source_url: str, img_url: str) -> bool:
    """True when the page or the image host is Wikipedia/Wikimedia."""
    host_src = (urlparse(source_url).hostname or "").lower()
    host_img = (urlparse(img_url).hostname or "").lower()
    return (
        any(h in host_src for h in _WIKI_HOSTS)
        or any(h in host_img for h in _WIKI_IMG_HOSTS)
    )


def _baike_alt(img, source_url: str, img_url: str) -> tuple[str, str]:
    """Baidu Baike lemma-picture caption, scoped to Baike pages.

    Three sources tried in order: the picture div's .titleSpan text, the
    wrapping <a title=...> attribute, and the data-single-image JSON's
    "title" field. Returns (text, from_label) where from_label ∈
    {"titlespan","a_title","json"}; ("","") off the Baike domain or when
    none yield text.
    """
    if not _is_baike(source_url, img_url):
        return ("", "")
    # Walk up to the lemma-picture container so sibling lookups work.
    container = img.find_parent(class_=re.compile(r"lemmaPicture"))
    if container is None:
        container = img.parent
    # 1. .titleSpan text.
    if container is not None:
        span = container.find(class_=re.compile(r"titleSpan"))
        if span is not None:
            txt = span.get_text(" ", strip=True)
            if txt:
                return (txt, "titlespan")
    # 2. wrapping <a title>.
    anchor = img.find_parent("a")
    if anchor is not None:
        a_title = (anchor.get("title") or "").strip()
        if a_title:
            return (a_title, "a_title")
    # 3. data-single-image JSON "title".
    if container is not None:
        raw = container.get("data-single-image") or ""
        if raw:
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                jt = (payload.get("title") or "").strip()
                if jt:
                    return (jt, "json")
    return ("", "")


def extract_images(
    html: str,
    source_url: str,
    source_title: str,
    roots: Optional[Sequence[str]] = _DEFAULT_CONTENT_ROOTS,
) -> List[ExtractedImage]:
    """Parse <img> tags from html, filter non-content images, return normalized list.

    `roots` is a sequence of CSS selectors tried in order; the first
    subtree that contains at least one <img> wins. If none match (or all
    are empty), the whole document is used so extraction still works on
    pages that lack the expected semantic landmarks. Pass an empty list
    to skip scoping entirely.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    if roots:
        scope = _resolve_scope(soup, roots)
    else:
        scope = soup
    out: List[ExtractedImage] = []
    # Per-page ALT_RESOLVE tallies for the summary probe.
    via_counts = {"figcaption": 0, "baike": 0, "filename": 0}
    had_alt = 0  # <img> already had a descriptive alt (no fallback needed)
    empty_alt = 0  # nothing yielded an alt (explicit empty + no fallback)
    for img in scope.find_all("img"):
        src = img.get("src") or ""
        if not src:
            continue
        if src.startswith("data:"):
            continue
        absolute = urljoin(source_url, src)
        scheme = urlparse(absolute).scheme.lower()
        if scheme not in ("http", "https"):
            continue
        if _is_blacklisted(absolute):
            continue
        width = _to_int(img.get("width"))
        height = _to_int(img.get("height"))
        # If a concrete dimension is present and below threshold, skip.
        if width is not None and width < _MIN_DIM:
            continue
        if height is not None and height < _MIN_DIM:
            continue
        alt_text, via, from_label = _resolve_alt(img, absolute, source_url)
        if via is None:
            if alt_text:
                had_alt += 1
            else:
                empty_alt += 1
        else:
            via_counts[via] = via_counts.get(via, 0) + 1
            # Per-image probe: fires only when a *supplemental* fallback
            # filled a non-empty alt (the wiki/baike/filename paths). The
            # explicit-alt and empty cases are covered by the summary's
            # had_alt/empty counts and by downstream CANDIDATE_NO_ALT.
            logger.info(
                f"[IMG-TRACE] ALT_RESOLVE via={via} from={from_label} "
                f"img_url={absolute} source_url={source_url} alt={alt_text!r}"
            )
        out.append(
            ExtractedImage(
                url=absolute,
                alt=alt_text,
                source_url=source_url,
                source_title=source_title,
                width=width,
                height=height,
            )
        )
    logger.info(
        f"[IMG-TRACE] ALT_RESOLVE_SUMMARY source_url={source_url} "
        f"images={len(out)} had_alt={had_alt} "
        f"via_figcaption={via_counts['figcaption']} "
        f"via_baike={via_counts['baike']} "
        f"via_filename={via_counts['filename']} "
        f"empty={empty_alt}"
    )
    return out
