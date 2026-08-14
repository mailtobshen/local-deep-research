"""darkweb engine factory and provenance tagging tests."""
from unittest.mock import patch, MagicMock

from local_deep_research.web_search_engines.darkweb import (
    _make_darkweb_engine,
    tag_darkweb,
)
from local_deep_research.web_search_engines.engine_registry import (
    ENGINE_REGISTRY,
    get_engine_entry,
)


def test_make_darkweb_engine_has_correct_params():
    """Engine must be configured to route via SearXNG's ahmia/torch + onions.

    SearXNGSearchEngine's __init__ calls safe_get to verify connectivity,
    which would fail in environments without searxng-ldr reachable. We
    patch safe_get so the test stays host-side hermetic.
    """
    fake_response = MagicMock()
    fake_response.cookies = {}
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_searxng.safe_get",
        return_value=fake_response,
    ):
        e = _make_darkweb_engine()
    assert e.engines == ["ahmia", "torch"]
    assert "onions" in e.categories
    assert e.max_results <= 10


def test_tag_darkweb_adds_provenance():
    results = [
        {"url": "http://aaa.onion/", "title": "x", "content": "y"},
        {"url": "http://bbb.onion/", "title": "x", "content": "y"},
    ]
    out = tag_darkweb(results)
    assert len(out) == 2
    for r in out:
        assert r.get("is_darkweb") is True
        assert r.get("metadata", {}).get("source") == "darkweb"
        # Source URL preserved.
        assert r["url"].endswith(".onion/")


def test_tag_darkweb_handles_empty():
    assert tag_darkweb([]) == []


def test_engine_registry_has_darkweb():
    assert "darkweb" in ENGINE_REGISTRY
    entry = get_engine_entry("darkweb")
    assert entry is not None
    assert entry.class_name == "SearXNGSearchEngine"