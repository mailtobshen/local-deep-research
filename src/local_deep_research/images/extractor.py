"""Extract real <img> from scraped HTML into a normalized list."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import unquote, urljoin, urlparse

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
    # Reject generic/non-descriptive single tokens.
    generic = {"x", "img", "image", "images", "photo", "file", "pic", "picture", "1", "2", "3"}
    if len(words) == 1 and words[0].lower() in generic:
        return ""
    return " ".join(words)


def _resolve_alt(img, absolute_url: str) -> str:
    """Return the best available alt text for an <img>, with fallbacks.

    Order: explicit alt → sibling <figcaption> (inside the nearest <figure>
    ancestor) → named entity parsed from the URL filename. Returns "" when
    none yield a descriptive value (preserves the empty-alt contract).
    """
    alt = (img.get("alt") or "").strip()
    if alt:
        return alt
    # Fallback 1: <figcaption> inside the enclosing <figure>.
    figure = img.find_parent("figure")
    if figure is not None:
        cap = figure.find("figcaption")
        if cap is not None:
            cap_text = cap.get_text(" ", strip=True)
            if cap_text:
                return cap_text
    # Fallback 2: named entity in the URL filename.
    return _alt_from_filename(absolute_url)


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
                alt=_resolve_alt(img, absolute),
                source_url=source_url,
                source_title=source_title,
                width=width,
                height=height,
            )
        )
    return out
