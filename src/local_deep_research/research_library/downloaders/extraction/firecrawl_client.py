"""Firecrawl HTTP client — wraps /v1/scrape, /v1/batch/scrape, /v1/search.

Pure HTTP client, no LDR engine-layer dependencies. Shared by the
fetch_content dispatch layer and the FirecrawlSearchEngine.
"""
from typing import Any, Dict, List, Optional

from loguru import logger

from ....security.safe_requests import safe_get, safe_post

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

    def scrape(self, url: str) -> Optional[str]:
        """Scrape a single URL, return markdown body or None on failure."""
        payload = {"url": url, "formats": ["markdown"]}
        try:
            resp = safe_post(
                f"{self.api_url}/v1/scrape",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            md = data.get("markdown")
            return md if isinstance(md, str) and md.strip() else None
        except Exception:
            logger.debug(f"Firecrawl scrape failed for {url}", exc_info=True)
            return None
