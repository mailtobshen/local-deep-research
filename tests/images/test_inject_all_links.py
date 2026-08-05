"""Verify _inject_all_links_of_system copies the cumulative list into
results (fix #1+#6 from
docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md)."""

from unittest.mock import MagicMock


def _make_system(links):
    sys = MagicMock()
    sys.all_links_of_system = list(links)
    return sys


def test_injects_cumulative_list():
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    results = {"findings": [{"search_results": []}]}
    sys = _make_system([{"link": "https://x/a", "html_content": "[]"}])

    out = _inject_all_links_of_system(results, sys)
    assert out is not results, "must return a shallow copy, not mutate input"
    assert results.get("all_links_of_system") is None, (
        "input dict must be untouched"
    )
    assert out["all_links_of_system"] == [
        {"link": "https://x/a", "html_content": "[]"}
    ]


def test_returns_input_unchanged_when_system_is_none():
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    results = {"findings": []}
    out = _inject_all_links_of_system(results, None)
    assert out is results
    assert "all_links_of_system" not in out


def test_returns_input_unchanged_when_no_cumulative_list():
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    results = {"findings": []}
    sys = MagicMock(spec=[])  # no all_links_of_system attr
    out = _inject_all_links_of_system(results, sys)
    assert out is results


def test_returns_input_unchanged_when_cumulative_empty():
    """Empty cumulative list is the same as absent (no-op)."""
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    results = {"findings": []}
    sys = _make_system([])
    out = _inject_all_links_of_system(results, sys)
    assert out is results
    assert "all_links_of_system" not in out