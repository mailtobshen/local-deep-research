"""Fix verification tests for the 2026-08-22 darkweb trio.

1. no_results abort gate: abort only fires on an EMPTY collector.
2. English query degradation: long English queries become short
   domain-noun probes for the darkweb engine.
3. Deferred-fill payload: text-only pages serialize as "[]", never
   raw markdown.
"""
from unittest.mock import MagicMock

from local_deep_research.utilities.cjk_query_split import (
    plan_darkweb_queries,
    shorten_english_query,
)


# --- Fix 2: English degradation -----------------------------------------


def test_long_english_query_degrades_to_domain_nouns():
    q = (
        "fentanyl trafficking supply chain manufacturing distribution "
        "criminal organizations United Nations Office on Drugs and "
        "Crime UNODC reports 2023 2024"
    )
    probes = shorten_english_query(q)
    assert probes, "degradation must yield probes for a long query"
    assert all(p in {"fentanyl", "trafficking"} for p in probes), probes
    # Domain noun fentanyl must not be crowded out by generic length.
    assert "fentanyl" in probes


def test_degradation_caps_at_max_terms():
    probes = shorten_english_query(
        "fentanyl cocaine methamphetamine heroin smuggling cartel", 2
    )
    assert len(probes) == 2


def test_cjk_query_untouched_by_degradation():
    assert shorten_english_query("芬太尼交易") == []


def test_stopword_only_query_yields_nothing():
    assert shorten_english_query("the and of reports 2024") == []


def test_cjk_plan_still_works():
    plan = plan_darkweb_queries("芬太尼及精神药物非法交易产业链")
    assert plan[0] == "芬太尼及精神药物非法交易产业链"
    assert "fentanyl" in plan


# --- Fix 1: abort gate (unit-level on the source contract) ---------------


def test_abort_gate_source_contract():
    """The abort condition must include a collector-empty check.

    Parses the strategy source and asserts the gate's shape — a full
    behavioral test needs a running langgraph, which the existing
    suite covers via test_langgraph_sources_allowlist fixtures.
    """
    import inspect
    from local_deep_research.advanced_search_system.strategies import (
        langgraph_agent_strategy as mod,
    )
    src = inspect.getsource(mod.LangGraphAgentStrategy.analyze_topic)
    assert "no_results_abort and not _collector_has_results" in src, (
        "abort gate must be collector-empty-guarded"
    )
    assert 'getattr(self.collector, "results", None)' in src


# --- Fix 3: payload serialization ----------------------------------------


def test_text_only_page_writes_empty_image_list():
    """The deferred-fill payload for a text-only page must be a JSON
    empty list (loads_images-clean), not raw markdown text."""
    import inspect
    from local_deep_research.web.services import research_service as rs
    src = inspect.getsource(rs._deferred_image_fill)
    # The `elif text:` branch must not assign `payload = text`.
    assert "elif text:\n            payload = text" not in src.replace(
        "\r", ""
    )
    assert 'payload = "[]"' in src
