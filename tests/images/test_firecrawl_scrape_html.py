# tests/images/test_firecrawl_scrape_html.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)

_MODPATH = "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post"


def test_scrape_returns_dict_with_markdown_and_html():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {"markdown": "# hi", "html": "<html><img src='x'></html>"}
    }
    with patch(_MODPATH, return_value=fake):
        result = client.scrape("https://example.com")
    assert isinstance(result, dict)
    assert result["markdown"] == "# hi"
    assert result["html"].startswith("<html")


def test_scrape_returns_none_on_http_error():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 500
    fake.json.return_value = {}
    with patch(_MODPATH, return_value=fake):
        assert client.scrape("https://example.com") is None
