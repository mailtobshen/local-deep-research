"""Extract real <img> from scraped HTML into a normalized list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

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
        out.append(
            ExtractedImage(
                url=absolute,
                alt=(img.get("alt") or "").strip(),
                source_url=source_url,
                source_title=source_title,
                width=width,
                height=height,
            )
        )
    return out
