from unittest.mock import patch, MagicMock

from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)


def _mock_response(status, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None if status < 400 else Exception("http error")
    return resp


def test_scrape_success():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    body = {"data": {"markdown": "# Title\n\nbody text"}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, body),
    ):
        result = client.scrape("https://example.com")
    assert result == "# Title\n\nbody text"
