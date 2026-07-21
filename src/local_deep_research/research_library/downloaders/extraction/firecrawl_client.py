"""Firecrawl HTTP client — wraps /v1/scrape, /v1/batch/scrape, /v1/search.

Pure HTTP client, no LDR engine-layer dependencies. Shared by the
fetch_content dispatch layer and the FirecrawlSearchEngine.
"""
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ....security.safe_requests import safe_get, safe_post
from ....web_search_engines.rate_limiting import RateLimitError

DEFAULT_API_URL = "http://localhost:3002"
DEFAULT_TIMEOUT = 30


class FirecrawlClient:
    """Thin client over a self-hosted Firecrawl instance.

    All calls go through safe_requests so SSRF + proxy-bypass rules apply.
    localhost/private-IP targets are allowed via allow_private_ips=True
    (Firecrawl is a trusted self-hosted service).
    """

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def scrape(
        self, url: str, include_html: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Scrape a single URL; return {markdown, html} or None on failure.

        html is requested only when include_html is True (gated upstream by
        report.enable_images). Raises RateLimitError on HTTP 429.
        """
        formats = ["markdown", "html"] if include_html else ["markdown"]
        payload = {"url": url, "formats": formats}
        try:
            resp = safe_post(
                f"{self.api_url}/v1/scrape",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
        except Exception:
            logger.debug(f"Firecrawl scrape request failed for {url}", exc_info=True)
            return None
        if resp.status_code == 429:
            raise RateLimitError("Firecrawl scrape rate limited")
        if resp.status_code >= 400:
            logger.debug(
                f"Firecrawl scrape failed for {url}: HTTP {resp.status_code}"
            )
            return None
        try:
            data = resp.json().get("data", {})
            md = data.get("markdown")
            if not (isinstance(md, str) and md.strip()):
                return None
            html = data.get("html")
            return {"markdown": md, "html": html if isinstance(html, str) else None}
        except Exception:
            logger.debug(f"Firecrawl scrape parse failed for {url}", exc_info=True)
            return None

    def batch_scrape(
        self,
        urls: List[str],
        max_wait: int = 60,
        poll_interval: int = 2,
    ) -> Dict[str, Optional[str]]:
        """Batch-scrape URLs. Returns {url: markdown|None}.

        Posts /v1/batch/scrape, polls /v1/batch/scrape/:jobId until
        completed or max_wait elapsed. URLs absent from the completed
        response are recorded as None. On any error returns all-None
        so the caller can fall back to the legacy pipeline.
        """
        result: Dict[str, Optional[str]] = dict.fromkeys(urls)
        if not urls:
            return result
        try:
            resp = safe_post(
                f"{self.api_url}/v1/batch/scrape",
                json={"urls": urls, "formats": ["markdown"]},
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
        except Exception:
            logger.debug("Firecrawl batch_scrape create failed", exc_info=True)
            return result
        if resp.status_code == 429:
            raise RateLimitError("Firecrawl batch_scrape rate limited")
        if resp.status_code >= 400:
            logger.debug(
                f"Firecrawl batch_scrape create failed: HTTP {resp.status_code}"
            )
            return result
        try:
            body = resp.json()
            job_id = body.get("id")
            if not job_id:
                return result
        except Exception:
            logger.debug("Firecrawl batch_scrape parse failed", exc_info=True)
            return result

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                poll = safe_get(
                    f"{self.api_url}/v1/batch/scrape/{job_id}",
                    headers=self._headers(),
                    timeout=self.timeout,
                    allow_private_ips=True,
                )
            except Exception:
                logger.debug(f"Firecrawl batch poll failed for {job_id}", exc_info=True)
                return result
            if poll.status_code == 429:
                raise RateLimitError("Firecrawl batch_scrape poll rate limited")
            if poll.status_code >= 400:
                logger.debug(
                    f"Firecrawl batch poll failed for {job_id}: HTTP {poll.status_code}"
                )
                return result
            try:
                pbody = poll.json()
            except Exception:
                logger.debug(
                    f"Firecrawl batch poll parse failed for {job_id}", exc_info=True
                )
                return result

            # Items can stream into `data` before status flips to "completed"
            # (self-hosted Firecrawl serves partial results while status is
            # still "scraping"), and the per-item url may live at the top
            # level OR under metadata.url. Accumulate from every poll.
            for item in pbody.get("data", []) or []:
                u = item.get("url") or (item.get("metadata") or {}).get("url")
                md = item.get("markdown")
                if u in result and isinstance(md, str) and md.strip():
                    result[u] = md

            # Done once the server says completed, or once we've resolved every
            # requested URL (defensive against servers that never set
            # status="completed").
            all_resolved = all(v is not None for v in result.values())
            if pbody.get("status") == "completed" or all_resolved:
                return result
            time.sleep(poll_interval)
        return result

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search via /v1/search. Returns list of {title,url,description,markdown}.

        Raises RateLimitError on HTTP 429 so the engine layer can propagate it.
        """
        try:
            resp = safe_post(
                f"{self.api_url}/v1/search",
                json={"query": query, "limit": limit},
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
        except Exception:
            logger.debug(f"Firecrawl search request failed for {query!r}", exc_info=True)
            return []
        if resp.status_code == 429:
            raise RateLimitError("Firecrawl search rate limited")
        if resp.status_code >= 400:
            logger.debug(
                f"Firecrawl search failed for {query!r}: HTTP {resp.status_code}"
            )
            return []
        try:
            data = resp.json().get("data", []) or []
        except Exception:
            logger.debug(f"Firecrawl search parse failed for {query!r}", exc_info=True)
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            md = item.get("markdown")
            out.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "markdown": md if isinstance(md, str) and md.strip() else None,
                }
            )
        return out
