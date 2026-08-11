from unittest.mock import patch, MagicMock

from local_deep_research.research_library.downloaders.extraction import (
    firecrawl_client,
)
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
    assert result == {"markdown": "# Title\n\nbody text", "html": None}


def test_scrape_with_html():
    """include_html=True passes through the html field when present."""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    body = {
        "data": {
            "markdown": "# Title",
            "html": "<p>body</p>",
        }
    }
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, body),
    ):
        result = client.scrape("https://example.com", include_html=True)
    assert result == {"markdown": "# Title", "html": "<p>body</p>"}


def test_scrape_failure_returns_none():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(500, {}),
    ):
        result = client.scrape("https://example.com")
    assert result is None


def test_batch_scrape_polls_until_complete():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(
        200, {"id": "job-1", "status": "processing"}
    )
    poll_processing = _mock_response(200, {"status": "processing", "completed": 0})
    poll_done = _mock_response(
        200,
        {
            "status": "completed",
            "completed": 2,
            "data": [
                {"url": "https://a.com", "markdown": "# A"},
                {"url": "https://b.com", "markdown": "# B"},
            ],
        },
    )
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            side_effect=[poll_processing, poll_done],
        ):
            with patch("time.sleep"):  # 加速轮询
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=60, poll_interval=1
                )
    assert result == {"https://a.com": "# A", "https://b.com": "# B"}


def test_batch_scrape_partial_failure():
    """完成回调里缺失的 URL 记 None，不抛异常。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(200, {"id": "job-1", "status": "processing"})
    poll_done = _mock_response(
        200,
        {
            "status": "completed",
            "completed": 1,
            "data": [{"url": "https://a.com", "markdown": "# A"}],
        },
    )
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            return_value=poll_done,
        ):
            with patch("time.sleep"):
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=60, poll_interval=1
                )
    assert result["https://a.com"] == "# A"
    assert result["https://b.com"] is None


def test_batch_scrape_timeout_returns_all_none():
    """超过 max_wait 仍未完成 -> 返回全 None（触发上层回落）。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(200, {"id": "job-1", "status": "processing"})
    poll_processing = _mock_response(200, {"status": "processing", "completed": 0})
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            return_value=poll_processing,
        ):
            with patch("time.sleep"):
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=0, poll_interval=1
                )
    assert result == {"https://a.com": None, "https://b.com": None}


def test_search_success():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    body = {
        "data": [
            {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
            {"title": "B", "url": "https://b.com", "description": "desc b", "markdown": None},
        ]
    }
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, body),
    ):
        results = client.search("query", limit=5)
    assert results == [
        {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
        {"title": "B", "url": "https://b.com", "description": "desc b", "markdown": None},
    ]


def test_search_failure_returns_empty():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(503, {}),
    ):
        results = client.search("query")
    assert results == []


def test_localhost_bypasses_proxy():
    """断言 safe_post 收到 allow_private_ips=True，避免 ollama-privoxy 回归。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, {"data": {"markdown": "x"}}),
    ) as mock_post:
        client.scrape("https://example.com")
    _, kwargs = mock_post.call_args
    assert kwargs.get("allow_private_ips") is True


def test_default_timeout_is_15():
    assert firecrawl_client.DEFAULT_TIMEOUT == 15
