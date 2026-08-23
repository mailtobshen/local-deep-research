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


# --- 2026-08-23 fan-out generic-word tightening (research 3e9ee493) ----


def test_generic_zh_fragments_dropped_from_plan():
    """'全球/中国/美国/供应链/报告' style fragments must not be issued
    as standalone sub-queries — they carry no topic signal and surface
    topically-unrelated onion mirrors (marxists archive et al.)."""
    for generic in ("全球", "中国", "美国", "报告", "供应链", "国际"):
        assert plan_darkweb_queries(f"芬太尼交易 {generic}") == [
            f"芬太尼交易 {generic}",
            "芬太尼交易",
            "fentanyl",
        ], generic


def test_generic_en_probes_dropped_from_degradation():
    """'supply'/'report' style probes must be dropped; the domain noun
    still wins the max_terms cap."""
    probes = shorten_english_query(
        "fentanyl supply chain China Mexico US DEA report 2023 2024"
    )
    assert probes, probes
    for generic in ("supply", "chain", "report", "china", "mexico"):
        assert generic not in [p.lower() for p in probes] or generic in (
            "china",
            "mexico",
        ), probes
    assert "supply" not in probes and "report" not in probes


def test_original_query_verbatim_still_first():
    plan = plan_darkweb_queries("芬太尼及精神药物非法交易产业链 全球 中国 美国")
    assert plan[0] == "芬太尼及精神药物非法交易产业链 全球 中国 美国"
    assert "fentanyl" in plan


def test_zero_overlap_demotion():
    """Relevance filter demotes zero-title-overlap + snippet-less
    previews to the end so a max_filtered_results cap drops them."""
    from local_deep_research.web_search_engines.relevance_filter import (
        _demote_zero_overlap_no_snippet,
    )

    previews = [
        {"title": "DEA Fentanyl Warn", "snippet": "s", "url": "u1"},
        {"title": "网上的马克思主义文库", "snippet": "", "url": "u2"},
        {"title": "Fentanyl shop", "snippet": "", "url": "u3"},
    ]
    out = _demote_zero_overlap_no_snippet(previews, "fentanyl china")
    assert [p["url"] for p in out] == ["u1", "u3", "u2"]
    # snippet alone rescues from demotion
    rescued = [{"title": "无关标题", "snippet": "有内容", "url": "u9"}]
    assert _demote_zero_overlap_no_snippet(rescued, "fentanyl") == rescued
