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
