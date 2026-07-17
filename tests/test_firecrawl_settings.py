"""Schema and value checks for the firecrawl settings block."""
import json
from pathlib import Path


def _defaults():
    p = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "local_deep_research"
        / "defaults"
        / "default_settings.json"
    )
    return json.loads(p.read_text())


def test_firecrawl_settings_present():
    d = _defaults()
    assert d["search.engine.web.firecrawl.enable"]["value"] is False
    assert d["search.engine.web.firecrawl.api_url"]["value"] == "http://localhost:3002"
    assert d["search.engine.web.firecrawl.use_for_content_fetch"]["value"] is False
    assert d["search.engine.web.firecrawl.search_mode"]["value"] == "firecrawl_search"
    assert d["search.engine.web.firecrawl.requires_api_key"]["value"] is False


def test_firecrawl_search_mode_has_options():
    d = _defaults()
    entry = d["search.engine.web.firecrawl.search_mode"]
    assert entry["ui_element"] == "select"
    assert entry["options"] == ["firecrawl_search", "ldr_search"]


def test_firecrawl_keys_match_tavily_schema_fields():
    """Every firecrawl entry must carry the same keys as a tavily entry."""
    d = _defaults()
    tavily_keys = set(d["search.engine.web.tavily.api_key"].keys())
    firecrawl_prefixes = {
        "search.engine.web.firecrawl.display_name",
        "search.engine.web.firecrawl.enable",
        "search.engine.web.firecrawl.api_url",
        "search.engine.web.firecrawl.api_key",
        "search.engine.web.firecrawl.use_for_content_fetch",
        "search.engine.web.firecrawl.search_mode",
    }
    for key in firecrawl_prefixes:
        assert set(d[key].keys()) == tavily_keys, key
