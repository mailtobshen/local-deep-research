# tests/images/test_firecrawl_scrape_html.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)

_MODPATH = "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post"


def test_scrape_include_html_true_requests_both_formats():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi", "html": "<html><img src='x'></html>"}}
    with patch(_MODPATH, return_value=fake) as sp:
        result = client.scrape("https://example.com", include_html=True)
    sent = sp.call_args.kwargs["json"]
    assert set(sent["formats"]) == {"markdown", "html"}
    assert result["html"].startswith("<html")


def test_scrape_default_requests_markdown_only():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi"}}
    with patch(_MODPATH, return_value=fake) as sp:
        result = client.scrape("https://example.com")
    sent = sp.call_args.kwargs["json"]
    assert sent["formats"] == ["markdown"]
    assert result["markdown"] == "# hi"
    assert result["html"] is None


def test_scrape_returns_none_on_http_error():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 500
    fake.json.return_value = {}
    with patch(_MODPATH, return_value=fake):
        assert client.scrape("https://example.com") is None