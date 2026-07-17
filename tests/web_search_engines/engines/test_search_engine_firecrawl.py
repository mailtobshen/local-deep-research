from unittest.mock import patch, MagicMock

from local_deep_research.web_search_engines.engines.search_engine_firecrawl import (
    FirecrawlSearchEngine,
)


def _make_engine(**over):
    base = dict(
        api_url="http://localhost:3002",
        api_key="fc-test",
        search_mode="firecrawl_search",
        max_results=5,
        settings_snapshot={},
    )
    base.update(over)
    return FirecrawlSearchEngine(**base)


def test_previews_firecrawl_search_mode():
    search_resp = [
        {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
    ]
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.search.return_value = search_resp
        engine = _make_engine()
        previews = engine._get_previews("query")
    assert previews[0]["title"] == "A"
    assert previews[0]["link"] == "https://a.com"
    assert previews[0]["snippet"] == "desc a"


def test_previews_empty_on_error():
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.search.side_effect = Exception("down")
        engine = _make_engine()
        previews = engine._get_previews("query")
    assert previews == []


def test_full_content_reuses_search_markdown():
    """previews 已带 markdown 时 _get_full_content 不再调 scrape。"""
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        engine = _make_engine()
        engine._search_results = [
            {
                "id": "https://a.com",
                "title": "A",
                "link": "https://a.com",
                "snippet": "desc a",
                "_full_result": {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
            }
        ]
        results = engine._get_full_content(engine._search_results)
        MockFC.return_value.scrape.assert_not_called()
    assert results[0]["content"] == "# A"


def test_full_content_falls_back_to_scrape():
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.scrape.return_value = "# Scraped"
        engine = _make_engine()
        item = {
            "id": "https://a.com",
            "title": "A",
            "link": "https://a.com",
            "snippet": "desc a",
            "_full_result": {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": None},
        }
        engine._search_results = [item]
        results = engine._get_full_content([item])
    assert results[0]["content"] == "# Scraped"


def test_previews_ldr_search_mode_delegates():
    """ldr_search 模式委托 preview fetcher。"""
    engine = _make_engine(search_mode="ldr_search")
    fake_fetcher = MagicMock()
    fake_fetcher._get_previews.return_value = [
        {"id": "u1", "title": "T1", "link": "https://a.com", "snippet": "s"}
    ]
    with patch.object(
        engine, "_build_ldr_preview_fetcher", return_value=fake_fetcher
    ):
        previews = engine._get_previews("query")
    fake_fetcher._get_previews.assert_called_once_with("query")
    assert previews[0]["link"] == "https://a.com"


def test_previews_ldr_search_no_source_returns_empty():
    engine = _make_engine(search_mode="ldr_search")
    with patch.object(engine, "_build_ldr_preview_fetcher", return_value=None):
        previews = engine._get_previews("query")
    assert previews == []


def test_rate_limit_reraised():
    """client.search 抛 RateLimitError 时引擎层应重抛，而非吞成 []。"""
    from local_deep_research.web_search_engines.rate_limiting import RateLimitError

    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.search.side_effect = RateLimitError("limited")
        engine = _make_engine()
        raised = False
        try:
            engine._get_previews("q")
        except RateLimitError:
            raised = True
    assert raised


def test_client_search_raises_rate_limit_on_429():
    """FirecrawlClient.search 收到 429 时应抛 RateLimitError。"""
    from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
        FirecrawlClient,
    )
    from local_deep_research.web_search_engines.rate_limiting import RateLimitError

    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    resp = MagicMock()
    resp.status_code = 429
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=resp,
    ):
        raised = False
        try:
            client.search("q")
        except RateLimitError:
            raised = True
    assert raised


def test_client_scrape_raises_rate_limit_on_429():
    """FirecrawlClient.scrape 收到 429 时应抛 RateLimitError。"""
    from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
        FirecrawlClient,
    )
    from local_deep_research.web_search_engines.rate_limiting import RateLimitError

    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    resp = MagicMock()
    resp.status_code = 429
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=resp,
    ):
        raised = False
        try:
            client.scrape("https://a.com")
        except RateLimitError:
            raised = True
    assert raised


def test_client_batch_scrape_raises_rate_limit_on_429():
    """FirecrawlClient.batch_scrape 创建请求收到 429 时应抛 RateLimitError。"""
    from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
        FirecrawlClient,
    )
    from local_deep_research.web_search_engines.rate_limiting import RateLimitError

    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    resp = MagicMock()
    resp.status_code = 429
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=resp,
    ):
        raised = False
        try:
            client.batch_scrape(["https://a.com"])
        except RateLimitError:
            raised = True
    assert raised
