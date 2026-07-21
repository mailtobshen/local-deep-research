from loguru import logger
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from langchain_core.language_models import BaseLLM

from ...config.search_config import QUALITY_CHECK_DDG_URLS
from ...config.thread_settings import get_bool_setting_from_snapshot
from ...images.serialize import dumps_images
from ...research_library.downloaders.extraction.pipeline import (
    fetch_content,
    fetch_content_with_images,
)
from ...security.ssrf_validator import validate_url
from ...utilities.js_rendering import (
    read_js_rendering_setting as _read_js_rendering_setting,
)
from ...utilities.json_utils import extract_json, get_llm_response_text


@runtime_checkable
class _Invokable(Protocol):
    def invoke(self, query: str) -> Any: ...


class FullSearchResults:
    def __init__(
        self,
        llm: Optional[BaseLLM],
        web_search: _Invokable,
        output_format: str = "list",
        language: str = "English",
        max_results: int = 10,
        region: str = "wt-wt",
        time: Optional[str] = "y",
        safesearch: str | int = "Moderate",
        settings_snapshot: Optional[Dict] = None,
    ):
        self.llm = llm
        self.output_format = output_format
        self.language = language
        self.max_results = max_results
        self.region = region
        self.time = time
        self.safesearch = safesearch
        self.web_search = web_search
        self.settings_snapshot = settings_snapshot

    def check_urls(self, results: List[Dict], query: str) -> List[Dict]:
        if not results:
            return results

        now = datetime.now(UTC)
        current_time = now.strftime("%Y-%m-%d")
        prompt = f"""ONLY Return a JSON array. The response contains no letters. Evaluate these URLs for:
            1. Timeliness (today: {current_time})
            2. Factual accuracy (cross-reference major claims)
            3. Source reliability (prefer official company websites, established news outlets)
            4. Direct relevance to query: {query}

            URLs to evaluate:
            {results}

            Return a JSON array of indices (0-based) for sources that meet ALL criteria.
            ONLY Return a JSON array of indices (0-based) and nothing else. No letters.
            Example response: \n[0, 2, 4]\n\n"""

        try:
            if self.llm is None:
                return results
            response = self.llm.invoke(prompt)
            response_text = get_llm_response_text(response)
            good_indices = extract_json(response_text, expected_type=list)

            if good_indices is None:
                good_indices = []

            return [r for i, r in enumerate(results) if i in good_indices]
        except Exception:
            logger.exception("URL filtering error")
            logger.warning(
                "URL quality filter unavailable — returning {} unfiltered "
                "results as fallback",
                len(results),
            )
            return results  # Fall back to original results on LLM error

    def run(self, query: str):
        # Step 1: Get search results
        search_results = self.web_search.invoke(query)
        if not isinstance(search_results, list):
            raise ValueError("Expected the search results in list format.")

        # Step 2: Filter URLs using LLM
        if QUALITY_CHECK_DDG_URLS:
            filtered_results = self.check_urls(search_results, query)
        else:
            filtered_results = search_results

        # Extract URLs from filtered results
        urls = [
            result.get("link")
            for result in filtered_results
            if result.get("link")
        ]

        if not urls:
            logger.error("\n === NO VALID LINKS ===\n")
            return []

        # SSRF-validate URLs
        safe_urls: List[str] = []
        for url in urls:
            if url is not None and validate_url(url):
                safe_urls.append(url)
            else:
                logger.warning(
                    f"SSRF validation blocked URL from full content fetch: {url}. "
                    "If this is a trusted internal/private resource, note that "
                    "full content fetching currently only supports public URLs."
                )

        if not safe_urls:
            logger.warning(
                "All URLs were blocked by SSRF validation — returning results "
                "without full content. This can happen when search results "
                "point to internal/private network addresses."
            )
            for result in filtered_results:
                result["full_content"] = None
            return filtered_results

        # Fetch and extract all pages — Firecrawl-first when enabled,
        # transparently falling back to specialized/HTML downloaders.
        url_to_content = fetch_content(
            safe_urls,
            settings_snapshot=self.settings_snapshot,
            language=self.language,
            enable_js_rendering=_read_js_rendering_setting(
                self.settings_snapshot
            ),
        )

        nr_full_text = sum(1 for v in url_to_content.values() if v)
        for result in filtered_results:
            link = result.get("link")
            result["full_content"] = url_to_content.get(link) if link else None

        logger.info(f"Full search: retrieved content from {nr_full_text} pages")
        return filtered_results

    def _get_full_content(
        self, relevant_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Fetch and attach full content to an existing list of items."""
        urls: List[str] = []
        for item in relevant_items:
            link = item.get("link")
            if link is not None and validate_url(link):
                urls.append(link)
            elif link is not None:
                logger.warning(
                    f"SSRF validation blocked URL from full content fetch: {link}."
                )

        if not urls:
            for item in relevant_items:
                item["full_content"] = None
            return relevant_items

        enable_images = get_bool_setting_from_snapshot(
            "report.enable_images",
            default=False,
            settings_snapshot=self.settings_snapshot,
        )

        if enable_images:
            titles = {
                item["link"]: item.get("title", "")
                for item in relevant_items
                if item.get("link")
            }
            try:
                url_to_data = fetch_content_with_images(
                    urls,
                    titles=titles,
                    settings_snapshot=self.settings_snapshot,
                    language=self.language,
                    enable_js_rendering=_read_js_rendering_setting(
                        self.settings_snapshot
                    ),
                )
            except Exception:
                logger.exception("Error fetching full content with images")
                url_to_data = {}
            for item in relevant_items:
                link = item.get("link")
                data = url_to_data.get(link) if link else None
                item["full_content"] = data.get("text") if data else None
                item["html_content"] = (
                    dumps_images(data["images"]) if data else dumps_images([])
                )
            return relevant_items

        try:
            url_to_content = fetch_content(
                urls,
                settings_snapshot=self.settings_snapshot,
                language=self.language,
                enable_js_rendering=_read_js_rendering_setting(
                    self.settings_snapshot
                ),
            )
        except Exception:
            logger.exception("Error fetching full content")
            for item in relevant_items:
                item["full_content"] = None
            return relevant_items

        for item in relevant_items:
            link = item.get("link")
            item["full_content"] = url_to_content.get(link) if link else None

        return relevant_items

    def invoke(self, query: str) -> Any:
        return self.run(query)

    def __call__(self, query: str) -> Any:
        return self.invoke(query)
