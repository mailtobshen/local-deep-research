"""
Shared extraction pipeline.

Two entry points:
    extract_content(html) — HTML string in, clean text out.
    fetch_and_extract(url) — URL in, clean text out (static + JS fallback).

This is the single source of truth for content extraction in the project.
Used by HTMLDownloader, ContentFetcher, FullSearchResults, WaybackSearchEngine,
and any other code that needs clean text from a web page.

Academic URLs (arXiv, PubMed, bioRxiv, etc.) are automatically routed to
specialized downloaders first, with the generic HTML pipeline as fallback.
"""

from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from loguru import logger

from ....web_search_engines.rate_limiting import RateLimitError

from .trafilatura_extractor import TrafilaturaExtractor
from .readability_extractor import ReadabilityExtractor
from .justext_extractor import JustextExtractor
from .newspaper_extractor import NewspaperExtractor
from .metadata_extractor import extract_metadata, metadata_to_text
from .firecrawl_client import FirecrawlClient
from ....config.thread_settings import (
    get_bool_setting_from_snapshot,
    get_setting_from_snapshot,
)
from ....images.extractor import extract_images


# Module-level placeholder so tests can ``patch.object(pipeline, "AutoHTMLDownloader", ...)``.
# A top-level ``from ..playwright_html import AutoHTMLDownloader`` would create a circular
# import (pipeline -> playwright_html -> html -> pipeline). The real class is
# resolved lazily inside ``fetch_content_with_images`` when this placeholder is None.
AutoHTMLDownloader = None


# --- Pipeline thresholds ---
# Minimum extracted text length to consider extraction successful
MIN_CONTENT_LENGTH = 50
# If content is shorter than this, enrich with structured metadata
# (JSON-LD, OpenGraph) — helps product pages, JS-heavy sites
METADATA_ENRICHMENT_THRESHOLD = 1000
# Boilerplate penalty per keyword when scoring extraction quality
BOILERPLATE_PENALTY = 500
# If an extractor discards more than this fraction of the previous
# extractor's output, skip it (protects non-English content)
SAFETY_DISCARD_RATIO = 0.2

# Module-level singleton extractors (avoid re-creating per call)
_trafilatura = TrafilaturaExtractor()
_readability = ReadabilityExtractor()
_justext_en = JustextExtractor(language="English")
_newspaper = NewspaperExtractor()

# Boilerplate keywords — used to penalize low-quality extraction
_BOILERPLATE_KEYWORDS = [
    "cookie",
    "sign up",
    "newsletter",
    "subscribe",
    "accept all",
    "privacy policy",
    "terms of service",
]


def _run_extractors_parallel(
    html: str, url: str
) -> tuple[str | None, str | None]:
    """Run trafilatura and newspaper4k sequentially.

    Both extractors call into lxml's C extension, which is not safe to
    share across threads — running them in a ThreadPoolExecutor caused
    Fatal Python error: Aborted during pool teardown on Python 3.14
    (the workers would deadlock in shutdown's join). Serializing the
    calls eliminates the crash; the perf cost is one extra extraction's
    worth of CPU per page, which is acceptable.

    The function name is preserved for backwards compatibility with
    any callers/tests that import it directly.
    """
    try:
        trafilatura_content = _trafilatura.extract(html)
    except Exception:
        logger.debug("Pipeline: trafilatura raised an exception")
        trafilatura_content = None

    try:
        newspaper_content = _newspaper.extract(html, url)
    except Exception:
        logger.debug("Pipeline: newspaper4k raised an exception")
        newspaper_content = None

    return trafilatura_content, newspaper_content


def _count_boilerplate(text: str) -> int:
    """Count boilerplate keyword occurrences in text."""
    if not text:
        return 0
    lower = text.lower()
    return sum(1 for kw in _BOILERPLATE_KEYWORDS if kw in lower)


def _quality_score(text: str) -> int:
    """Score extraction quality: length minus boilerplate penalty."""
    if not text:
        return 0
    return len(text) - (_count_boilerplate(text) * BOILERPLATE_PENALTY)


def extract_content(
    html: str,
    language: str = "English",
    min_length: int = MIN_CONTENT_LENGTH,
    url: str = "",
) -> Optional[str]:
    """Extract clean text content from HTML.

    Pipeline:
        1. trafilatura (primary — best benchmarks, multilingual, markdown)
        2. newspaper4k (parallel — strong on news/forum pages)
        → pick the higher-quality result from steps 1-2
        3. readability → justext (fallback if both above fail, with 80% safety)
        4. soup.get_text() (last resort)

    Args:
        html: Raw HTML string.
        language: Language for justext stoplist (fallback only).
        min_length: Minimum content length to accept.
        url: Source URL (improves newspaper4k extraction accuracy).

    Returns:
        Extracted plain text, or None if content is below min_length.
    """
    if not html or not html.strip():
        return None

    # Run trafilatura and newspaper4k in parallel, pick the better result.
    # newspaper4k is strong on news front pages and multi-answer threads
    # where trafilatura sometimes extracts less content.
    # 5s timeout per extractor — covers P95 of pages, cuts off outliers.
    trafilatura_content, newspaper_content = _run_extractors_parallel(html, url)

    traf_score = _quality_score(trafilatura_content)
    np_score = _quality_score(newspaper_content)

    if traf_score >= np_score and trafilatura_content:
        content = trafilatura_content
        winner = "trafilatura"
    elif newspaper_content:
        content = newspaper_content
        winner = "newspaper4k"
    else:
        content = trafilatura_content
        winner = "trafilatura"

    if content and len(content.strip()) >= min_length:
        logger.debug(
            f"Pipeline: {winner} extracted {len(content)} chars"
            + (
                f" (traf={len(trafilatura_content or '')}, "
                f"np4k={len(newspaper_content or '')})"
                if newspaper_content and trafilatura_content
                else ""
            )
        )
    else:
        # Fallback: readability → justext
        logger.debug(
            "Pipeline: primary extractors insufficient, using fallback"
        )

        soup = BeautifulSoup(html, "html.parser")
        for tag_name in [
            "script",
            "style",
            "iframe",
            "noscript",
            "svg",
            "form",
            "button",
            "input",
            "select",
            "textarea",
        ]:
            for tag in soup.find_all(tag_name):
                tag.decompose()
        cleaned_html = str(soup)

        justext_extractor = (
            _justext_en
            if language == "English"
            else JustextExtractor(language=language)
        )

        content = None
        prev_text_len = 0

        for extractor in [_readability, justext_extractor]:
            result = extractor.extract(
                cleaned_html if prev_text_len == 0 else content
            )
            if result and result.strip():
                result_len = len(result.strip())
                # Safety: skip if extractor discards >80% of content.
                # Compare text lengths (strip HTML tags for fair comparison
                # since readability returns HTML but justext returns text).
                if (
                    prev_text_len > 0
                    and result_len < prev_text_len * SAFETY_DISCARD_RATIO
                ):
                    logger.debug(
                        f"Pipeline: {extractor.__class__.__name__} discarded "
                        f">80% of content — skipping"
                    )
                    continue
                content = result
                # Store text-equivalent length for fair comparison
                if "<" in result:
                    prev_text_len = len(
                        BeautifulSoup(result, "html.parser").get_text()
                    )
                else:
                    prev_text_len = result_len
                logger.debug(
                    f"Pipeline: {extractor.__class__.__name__} "
                    f"returned {result_len} chars"
                )

        # Strip remaining HTML tags (e.g. readability-only mode)
        if content and "<" in content:
            content = BeautifulSoup(content, "html.parser").get_text(
                separator="\n", strip=True
            )

        # Last resort
        if not content or len(content.strip()) < min_length:
            logger.debug("Pipeline: all extractors failed, using get_text()")
            content = soup.get_text(separator="\n", strip=True)

    if not content or len(content.strip()) < min_length:
        return None

    # Enrich with structured metadata when text extraction is thin
    # (e.g. product pages, JS-heavy sites)
    if len(content.strip()) < METADATA_ENRICHMENT_THRESHOLD:
        metadata = extract_metadata(html)
        supplement = metadata_to_text(metadata)
        if supplement and supplement.strip():
            logger.debug(
                f"Pipeline: enriching with {len(supplement)} chars "
                f"of structured metadata"
            )
            content = content.rstrip() + "\n\n" + supplement

    return content


def extract_content_with_metadata(
    html: str,
    language: str = "English",
    min_length: int = MIN_CONTENT_LENGTH,
) -> Optional[Dict[str, Any]]:
    """Extract clean text and page metadata from HTML in a single pass.

    Combines content extraction (trafilatura/readability/justext pipeline)
    with title and description extraction from HTML meta tags. This avoids
    the need for callers to do a separate BeautifulSoup parse for metadata.

    Args:
        html: Raw HTML string.
        language: Language for justext stoplist (fallback only).
        min_length: Minimum content length to accept.

    Returns:
        Dict with keys: content, title, description — or None if content
        is below min_length.
    """
    if not html or not html.strip():
        return None

    # Single parse for metadata (title, description, og:*)
    soup = BeautifulSoup(html, "html.parser")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = str(og_title["content"]).strip()

    description = None
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = str(meta_desc["content"]).strip()
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = str(og_desc["content"]).strip()

    # Extract content using the shared pipeline
    content = extract_content(html, language=language, min_length=min_length)
    if not content:
        return None

    return {
        "title": title,
        "description": description,
        "content": content,
    }


def _try_specialized_downloader(url: str, timeout: int = 30) -> Optional[str]:
    """Try a specialized downloader (arXiv, PubMed, etc.) for the URL.

    Returns extracted text if a specialized downloader handles this URL
    and succeeds, or None to signal "fall back to generic HTML pipeline".
    """
    try:
        from local_deep_research.content_fetcher.url_classifier import (
            URLClassifier,
            URLType,
        )
    except ImportError:
        return None

    url_type = URLClassifier.classify(url)

    # Only academic URL types have specialized downloaders worth trying.
    # HTML, DOI, PDF, INVALID fall through to the generic pipeline.
    _SPECIALIZED_TYPES = {
        URLType.ARXIV,
        URLType.PUBMED,
        URLType.PMC,
        URLType.SEMANTIC_SCHOLAR,
        URLType.BIORXIV,
        URLType.MEDRXIV,
    }
    if url_type not in _SPECIALIZED_TYPES:
        return None

    # Map URL type to downloader class (lazy imports to avoid circular deps)
    downloader = None
    try:
        if url_type == URLType.ARXIV:
            from ..arxiv import ArxivDownloader

            downloader = ArxivDownloader(timeout=timeout)
        elif url_type in (URLType.PUBMED, URLType.PMC):
            from ..pubmed import PubMedDownloader

            downloader = PubMedDownloader(timeout=timeout)
        elif url_type == URLType.SEMANTIC_SCHOLAR:
            from ..semantic_scholar import SemanticScholarDownloader

            downloader = SemanticScholarDownloader(timeout=timeout)
        elif url_type in (URLType.BIORXIV, URLType.MEDRXIV):
            from ..biorxiv import BioRxivDownloader

            downloader = BioRxivDownloader(timeout=timeout)
    except ImportError:
        logger.debug(
            f"Pipeline: specialized downloader not available for {url_type.value}"
        )
        return None

    if not downloader:
        return None

    try:
        from ..base import ContentType

        result = downloader.download_with_result(url, ContentType.TEXT)
        if result.is_success and result.content:
            text = result.content.decode("utf-8", errors="replace")
            if len(text.strip()) >= MIN_CONTENT_LENGTH:
                logger.debug(
                    f"Pipeline: specialized downloader ({url_type.value}) "
                    f"returned {len(text)} chars for {url}"
                )
                return text
    except Exception:
        logger.debug(
            f"Pipeline: specialized downloader failed for {url}",
            exc_info=True,
        )
    finally:
        try:
            downloader.close()
        except Exception:  # noqa: silent-exception
            pass

    # Specialized downloader didn't produce content — fall back to HTML
    logger.debug(
        f"Pipeline: specialized downloader ({url_type.value}) returned "
        f"no content for {url}, falling back to HTML pipeline"
    )
    return None


def fetch_and_extract(
    url: str,
    timeout: int = 30,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Optional[str]:
    """Fetch a URL and extract clean text content.

    Pipeline:
        1. Specialized downloader (arXiv PDF, PubMed API, etc.) if URL matches
        2. Static HTTP fetch → Playwright fallback (if JS needed and
           ``enable_js_rendering`` is True) → trafilatura → readability →
           justext

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.
        language: Language for justext stoplist.
        enable_js_rendering: When True, the HTML pipeline falls back to a
            headless browser for pages that need JavaScript. Defaults to
            False because the default Docker production image ships without
            Chromium. Limited internal benchmark comparisons (dev instances
            with Chromium vs Docker without) showed no measurable
            research-quality improvement from JS rendering, and most regular
            benchmark runs are on Docker without Chromium anyway. The
            user-facing toggle is ``web.enable_javascript_rendering``.

    Returns:
        Extracted plain text, or None if fetch or extraction failed.
    """
    # Try specialized downloader first (arXiv, PubMed, etc.)
    specialized = _try_specialized_downloader(url, timeout=timeout)
    if specialized:
        return specialized

    # Generic HTML pipeline
    from ..playwright_html import AutoHTMLDownloader

    downloader = AutoHTMLDownloader(
        timeout=timeout,
        language=language,
        enable_js_rendering=enable_js_rendering,
    )
    try:
        # download() returns extracted text as UTF-8 bytes (not raw HTML):
        # AutoHTMLDownloader inherits HTMLDownloader.download() which runs
        # _fetch_html() → _extract_content() → the full extraction pipeline.
        result = downloader.download(url)
        if result:
            return result.decode("utf-8", errors="replace")
        return None
    except Exception:
        logger.exception(f"fetch_and_extract failed for {url}")
        return None
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in fetch_and_extract")


def _firecrawl_enabled(settings_snapshot: Optional[dict]) -> bool:
    """Both the master switch and the content-fetch switch must be on."""
    enable = get_bool_setting_from_snapshot(
        "search.engine.web.firecrawl.enable",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    use_for_content = get_bool_setting_from_snapshot(
        "search.engine.web.firecrawl.use_for_content_fetch",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    return bool(enable and use_for_content)


def _new_firecrawl_client_from_snapshot(
    settings_snapshot: Optional[dict],
) -> FirecrawlClient:
    """Build a FirecrawlClient from settings (api_url / api_key)."""
    api_url = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_url",
        default="http://localhost:3002",
        settings_snapshot=settings_snapshot,
    )
    api_url = (
        api_url
        if isinstance(api_url, str) and api_url
        else "http://localhost:3002"
    )
    # Use a non-None sentinel default so missing keys don't raise
    # NoSettingsContextError (FirecrawlClient accepts None for api_key).
    api_key = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_key",
        default="",
        settings_snapshot=settings_snapshot,
    )
    api_key = api_key if isinstance(api_key, str) and api_key else None
    return FirecrawlClient(api_url=api_url, api_key=api_key)


def fetch_content(
    urls: List[str],
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Optional[str]]:
    """Fetch + extract text for urls.

    Thin wrapper over `_fetch_content_dispatcher` with image extraction
    disabled; returns only the `text` field per url. Behavior is the same
    as `batch_fetch_and_extract` for default config, and uses Playwright
    first / Firecrawl fallback when `firecrawl.use_for_content_fetch` is
    on (per `_firecrawl_enabled`).
    """
    data = _fetch_content_dispatcher(
        urls,
        titles=None,
        settings_snapshot=settings_snapshot,
        language=language,
        enable_js_rendering=enable_js_rendering,
        enable_images=False,
    )
    return {url: (entry.get("text") if entry else None) for url, entry in data.items()}


def fetch_content_with_images(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Fetch + extract text AND images from the same download.

    Returns {url: {"text": Optional[str], "images": List[ExtractedImage]}}.
    Image extraction never affects text extraction (isolated try/except).
    No extra network request: images come from the already-fetched HTML.
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not urls:
        return result

    titles = titles or {}
    # Lazy-resolve AutoHTMLDownloader: a top-level import would create a
    # circular import (pipeline -> playwright_html -> html -> pipeline).
    # If a previous attempt's deferred module-level binding left the
    # placeholder as None, fall through to a real import so production
    # calls still work. ImportError is intentionally NOT caught here — a
    # real cycle means a real bug, suppression would hide it.
    dl_cls = AutoHTMLDownloader
    if dl_cls is None:
        from ..playwright_html import AutoHTMLDownloader as _dl_cls

        dl_cls = _dl_cls
    downloader = dl_cls(
        timeout=30,
        language=language,
        enable_js_rendering=enable_js_rendering,
    )
    try:
        for url in urls:
            text: Optional[str] = None
            images = []
            try:
                text_bytes, raw_html = downloader.download_with_html(url)
                if text_bytes:
                    text = text_bytes.decode("utf-8", errors="replace")
                if raw_html:
                    try:
                        images = extract_images(
                            raw_html, url, titles.get(url, "")
                        )
                    except Exception:
                        logger.debug(
                            "extract_images failed for %s", url, exc_info=True
                        )
                        images = []
            except Exception:
                logger.debug(
                    "fetch_content_with_images failed for %s", url, exc_info=True
                )
            result[url] = {"text": text, "images": images}
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in fetch_content_with_images")

    return result


def _fetch_content_dispatcher(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
    enable_images: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Single fetcher+image-extraction dispatcher.

    For each url:
      1. Playwright download (text + raw_html) via download_with_html()
      2. If Playwright yields text → extract images if enable_images
      3. Else if _firecrawl_enabled(snapshot) →
         single scrape(link, include_html=enable_images); text=markdown, images from html
      4. Else → {text: None, images: []}
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not urls:
        return result

    titles = titles or {}
    # Lazy-resolve AutoHTMLDownloader: see fetch_content_with_images for the
    # circular-import rationale (pipeline -> playwright_html -> html -> pipeline).
    dl_cls = AutoHTMLDownloader
    if dl_cls is None:
        from ..playwright_html import AutoHTMLDownloader as _dl_cls

        dl_cls = _dl_cls
    downloader = dl_cls(
        timeout=30,
        language=language,
        enable_js_rendering=enable_js_rendering,
    )
    # Pre-compute once before the loop (snapshot doesn't change per URL).
    firecrawl_enabled = _firecrawl_enabled(settings_snapshot)
    firecrawl_client = None
    if firecrawl_enabled:
        try:
            firecrawl_client = _new_firecrawl_client_from_snapshot(
                settings_snapshot
            )
        except Exception:
            logger.debug("Failed to build Firecrawl client in dispatcher",
                         exc_info=True)
            firecrawl_client = None
            firecrawl_enabled = False

    try:
        for url in urls:
            text: Optional[str] = None
            images: List = []
            pw_failed = False
            try:
                text_bytes, raw_html = downloader.download_with_html(url)
                if text_bytes:
                    text = text_bytes.decode("utf-8", errors="replace")
                else:
                    pw_failed = True
                if enable_images and raw_html:
                    try:
                        images = extract_images(
                            raw_html, url, titles.get(url, "")
                        )
                    except Exception:
                        logger.debug(
                            "extract_images failed for %s", url, exc_info=True
                        )
                        images = []
            except Exception:
                logger.debug(
                    "_fetch_content_dispatcher Playwright path failed for %s",
                    url,
                    exc_info=True,
                )
                pw_failed = True

            if pw_failed and firecrawl_enabled and firecrawl_client is not None:
                try:
                    response = firecrawl_client.scrape(
                        url, include_html=enable_images
                    )
                except Exception:
                    logger.debug(
                        "Firecrawl fallback failed for %s", url, exc_info=True
                    )
                    response = None
                if isinstance(response, dict):
                    md = response.get("markdown")
                    if isinstance(md, str) and md.strip():
                        text = md
                    html_from_fc = response.get("html")
                    if (
                        enable_images
                        and isinstance(html_from_fc, str)
                        and html_from_fc
                    ):
                        try:
                            images = extract_images(
                                html_from_fc, url, titles.get(url, "")
                            )
                        except Exception:
                            logger.debug(
                                "extract_images failed on Firecrawl html for %s",
                                url,
                                exc_info=True,
                            )
                            images = []

            result[url] = {"text": text, "images": images}
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in dispatcher")

    return result


def batch_fetch_and_extract(
    urls: List[str],
    timeout: int = 30,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Optional[str]]:
    """Fetch multiple URLs and extract clean text from each.

    For each URL:
        1. Try specialized downloader (arXiv, PubMed, etc.) if URL matches
        2. Fall back to generic HTML pipeline (AutoHTMLDownloader)

    Uses a single AutoHTMLDownloader (and thus a single Playwright
    browser if JS fallback is triggered) for the generic HTML URLs.

    Args:
        urls: List of URLs to fetch.
        timeout: Request timeout in seconds per URL.
        language: Language for justext stoplist.
        enable_js_rendering: When True, the HTML pipeline falls back to a
            headless browser for pages that need JavaScript. Defaults to
            False because the default Docker production image ships without
            Chromium. Limited internal benchmark comparisons (dev instances
            with Chromium vs Docker without) showed no measurable
            research-quality improvement from JS rendering, and most regular
            benchmark runs are on Docker without Chromium anyway. The
            user-facing toggle is ``web.enable_javascript_rendering``.

    Returns:
        Dict mapping URL → extracted text (or None if failed).
    """
    from ..playwright_html import AutoHTMLDownloader

    results: Dict[str, Optional[str]] = {}

    # Try specialized downloaders first — collect URLs that need HTML fallback
    html_urls: List[str] = []
    for url in urls:
        try:
            specialized = _try_specialized_downloader(url, timeout=timeout)
            if specialized:
                results[url] = specialized
                continue
        except Exception:
            logger.debug(
                f"Pipeline: specialized downloader error for {url}",
                exc_info=True,
            )
        html_urls.append(url)

    # Generic HTML pipeline for remaining URLs
    if html_urls:
        downloader = AutoHTMLDownloader(
            timeout=timeout,
            language=language,
            enable_js_rendering=enable_js_rendering,
        )
        try:
            for url in html_urls:
                try:
                    data = downloader.download(url)
                    if data:
                        results[url] = data.decode("utf-8", errors="replace")
                    else:
                        results[url] = None
                except Exception:
                    logger.exception(
                        f"batch_fetch_and_extract failed for {url}"
                    )
                    results[url] = None
        finally:
            try:
                downloader.close()
            except Exception:
                logger.debug(
                    "Failed to close downloader in batch_fetch_and_extract"
                )

    return results
