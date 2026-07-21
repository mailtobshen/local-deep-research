from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseLLM
from loguru import logger

from ...config.thread_settings import get_setting_from_snapshot
from ...config.thread_settings import get_bool_setting_from_snapshot
from ...images.extractor import extract_images
from ...images.serialize import dumps_images
from ...research_library.downloaders.extraction.firecrawl_client import FirecrawlClient
from ..rate_limiting import RateLimitError  # noqa: F401  (re-exported convention)
from ..search_engine_base import BaseSearchEngine


class FirecrawlSearchEngine(BaseSearchEngine):
    """Search engine backed by a self-hosted Firecrawl instance.

    search_mode:
      - "firecrawl_search": use /v1/search (Firecrawl searches + scrapes)
      - "ldr_search": use an LDR preview source (SearXNG, fallback DDG) for
        links, then Firecrawl only for full-content scraping
    """

    is_public = True
    is_generic = True

    def __init__(
        self,
        max_results: int = 10,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        search_mode: Optional[str] = None,
        llm: Optional[BaseLLM] = None,
        include_full_content: bool = True,
        max_filtered_results: Optional[int] = None,
        settings_snapshot: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(
            llm=llm,
            max_filtered_results=max_filtered_results,
            max_results=max_results,
            include_full_content=include_full_content,
            # For this engine, full content is fetched via Firecrawl scrape in
            # _get_full_content, so snippet-only mode is the inverse of
            # include_full_content. Without this, the base run() defaults
            # search_snippets_only=True and never calls _get_full_content.
            search_snippets_only=not include_full_content,
            settings_snapshot=settings_snapshot,
        )

        # The factory does not thread engine-specific scalar keys from the
        # settings snapshot into __init__, so read them here (same pattern as
        # the Paperless engine). Explicit kwargs take precedence.
        self.search_mode = search_mode or get_setting_from_snapshot(
            "search.engine.web.firecrawl.search_mode",
            default="firecrawl_search",
            settings_snapshot=settings_snapshot,
        )
        self.api_url = api_url or get_setting_from_snapshot(
            "search.engine.web.firecrawl.api_url",
            default="http://localhost:3002",
            settings_snapshot=settings_snapshot,
        )

        # Firecrawl is typically self-hosted, so an API key is optional.
        # Resolve one if available, but fall back to "" (no auth) rather than
        # raising — the self-hosted server may not require a bearer token.
        try:
            self.api_key = self._resolve_api_key(
                api_key,
                "search.engine.web.firecrawl.api_key",
                engine_name="Firecrawl",
                settings_snapshot=settings_snapshot,
            )
        except ValueError:
            self.api_key = ""
        self._client = FirecrawlClient(api_url=self.api_url, api_key=self.api_key)

    def _get_previews(self, query: str) -> List[Dict[str, Any]]:
        if self.search_mode == "ldr_search":
            return self._get_previews_ldr(query)
        return self._get_previews_firecrawl(query)

    def _get_previews_firecrawl(self, query: str) -> List[Dict[str, Any]]:
        try:
            results = self._client.search(query, limit=self.max_results)
        except RateLimitError:
            raise
        except Exception:
            logger.exception("Firecrawl search failed")
            return []
        previews = []
        for i, r in enumerate(results):
            preview = {
                "id": r.get("url", str(i)),
                "title": r.get("title", ""),
                "link": r.get("url", ""),
                "snippet": r.get("description", ""),
                "displayed_link": r.get("url", ""),
                "position": i,
                "_full_result": r,
            }
            previews.append(preview)
        self._search_results = previews
        return previews

    def _get_previews_ldr(self, query: str) -> List[Dict[str, Any]]:
        """Delegate link discovery to an LDR preview source (SearXNG→DDG)."""
        fetcher = self._build_ldr_preview_fetcher()
        if fetcher is None:
            logger.warning("No LDR preview source available for firecrawl ldr_search")
            return []
        try:
            previews = fetcher._get_previews(query)
        except Exception:
            logger.exception("LDR preview fetcher failed")
            return []
        self._search_results = previews
        return previews

    def _build_ldr_preview_fetcher(self):
        """Return a SearXNG or DDG engine instance for preview fetching."""
        try:
            from .search_engine_searxng import SearXNGSearchEngine

            return SearXNGSearchEngine(
                max_results=self.max_results,
                settings_snapshot=self.settings_snapshot,
            )
        except Exception:
            pass
        try:
            from .search_engine_ddg import DuckDuckGoSearchEngine

            return DuckDuckGoSearchEngine(
                max_results=self.max_results,
                settings_snapshot=self.settings_snapshot,
            )
        except Exception:
            return None

    def _get_full_content(
        self, relevant_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        enable_images = get_bool_setting_from_snapshot(
            "report.enable_images",
            default=False,
            settings_snapshot=self.settings_snapshot,
        )
        results = []
        for item in relevant_items:
            full = item.get("_full_result") or {}
            md = full.get("markdown")
            html = full.get("html")
            if not (isinstance(md, str) and md.strip()):
                link = item.get("link")
                if link:
                    try:
                        scraped = self._client.scrape(link, include_html=enable_images)
                    except Exception:
                        logger.debug(
                            f"Firecrawl scrape failed for {link}", exc_info=True
                        )
                        scraped = None
                    if isinstance(scraped, dict):
                        md = scraped.get("markdown")
                        html = scraped.get("html")
            item = dict(item)
            item["content"] = md or item.get("content", "")
            if enable_images:
                images = []
                if isinstance(html, str) and html:
                    try:
                        images = extract_images(
                            html, item.get("link", ""), item.get("title", "")
                        )
                    except Exception:
                        logger.debug("extract_images failed", exc_info=True)
                        images = []
                item["html_content"] = dumps_images(images)
            results.append(item)
        return results
