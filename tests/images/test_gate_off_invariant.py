"""Locks the unified-gate byte-for-byte invariant: when report.enable_images
is OFF (default), neither Firecrawl scrape() nor FullSearchResults requests
extra image data, and items do not get an html_content field.
"""
from unittest.mock import MagicMock, patch

from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)
from local_deep_research.web_search_engines.engines.search_engine_firecrawl import (
    FirecrawlSearchEngine,
)
from local_deep_research.web_search_engines.engines.full_search import (
    FullSearchResults,
)


def test_firecrawl_scrape_off_requests_markdown_only():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi"}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=fake,
    ) as sp:
        client.scrape("https://example.com")
    sent = sp.call_args.kwargs["json"]
    assert sent["formats"] == ["markdown"]


def test_full_search_off_uses_plain_fetch_content():
    fs = FullSearchResults(llm=None, web_search=MagicMock(), settings_snapshot={})
    items = [{"link": "https://src/p", "title": "P"}]
    with patch(
        "local_deep_research.web_search_engines.engines.full_search.get_bool_setting_from_snapshot",
        return_value=False,
    ), patch(
        "local_deep_research.web_search_engines.engines.full_search.validate_url",
        return_value=True,
    ), patch(
        "local_deep_research.web_search_engines.engines.full_search.fetch_content",
        return_value={"https://src/p": "body"},
    ) as fc, patch(
        "local_deep_research.web_search_engines.engines.full_search.fetch_content_with_images",
    ) as fcwi:
        out = fs._get_full_content(items)
    fc.assert_called_once()
    fcwi.assert_not_called()
    assert "html_content" not in out[0]


def test_all_images_modules_importable():
    import importlib

    for name in (
        "local_deep_research.images",
        "local_deep_research.images.serialize",
        "local_deep_research.images.postprocessing",
        "local_deep_research.images.extractor",
        "local_deep_research.images.bank",
        "local_deep_research.images.enhancer",
        "local_deep_research.images.store",
        "local_deep_research.images.vision",
        "local_deep_research.web_search_engines.engines.full_search",
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl",
        "local_deep_research.research_library.downloaders.html",
        "local_deep_research.research_library.downloaders.extraction.pipeline",
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client",
    ):
        importlib.import_module(name)