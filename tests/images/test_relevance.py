"""Tests for the strict context-entity image relevance gate."""
from __future__ import annotations

import pytest

from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.relevance import (
    build_report_entity_context,
    evaluate_candidate,
)


def candidate(alt, source="https://source.example/page"):
    return ExtractedImage(
        url="https://img.example/a.jpg",
        alt=alt,
        source_url=source,
        source_title="",
        width=None,
        height=None,
    )


# ---------------------------------------------------------------------------
# Step 1 — generic / compound generic rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alt",
    ["旅游景点攻略", "旅游攻略", "热门景点", "精彩图片", "古建筑"],
)
def test_compound_generic_is_rejected(alt):
    context = build_report_entity_context(
        "# 广州近代建筑\n## 广州塔\n广州塔是地标。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate(alt), context)
    assert decision.status == "drop"
    assert decision.reason == "no_named_entity"


def test_single_generic_word_is_rejected():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 广州塔\n广州塔是地标。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("夜景"), context)
    assert decision.status == "drop"
    assert decision.reason == "no_named_entity"


# ---------------------------------------------------------------------------
# Step 2 — foreign-entity conflict detection
# ---------------------------------------------------------------------------


def test_foreign_entity_chongqing_is_rejected():
    """Alt mentioning 重庆 from an Instagram page about 广州 is a conflict."""
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔。",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://instagram.example/popular/广州景点",
                            "title": "广州景点",
                        }
                    ]
                }
            ]
        },
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate(
            "重庆洪崖洞旅游攻略",
            "https://instagram.example/popular/广州景点",
        ),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason == "foreign_entity_conflict"
    assert decision.source_signal == "weak"


def test_foreign_entity_first_time_chongqing_is_rejected():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔。",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://instagram.example/popular/广州景点",
                            "title": "广州景点",
                        }
                    ]
                }
            ]
        },
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate(
            "第一次来重庆，别只玩市区的景点",
            "https://instagram.example/popular/广州景点",
        ),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason == "foreign_entity_conflict"


def test_foreign_entity_jiangxi_wuyuan_is_rejected():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔。",
        {"findings": []},
        query="广州旅游",
    )
    decision = evaluate_candidate(candidate("江西婺源古村落"), context)
    assert decision.status == "drop"
    assert decision.reason == "foreign_entity_conflict"


# ---------------------------------------------------------------------------
# Step 3 — context match (kept)
# ---------------------------------------------------------------------------


def test_context_match_guangzhou_tower():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州塔珠江夜景\n广州塔是地标，珠江夜游不可错过。",
        {"findings": []},
        query="广州旅游",
    )
    decision = evaluate_candidate(candidate("广州塔珠江夜景"), context)
    assert decision.status == "keep"
    assert decision.reason in ("context_match", "context_entity_rescue")
    assert 1 in decision.matched_sections


def test_context_match_section_heading_zhongshan():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 中山纪念堂\n中山纪念堂位于广州。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert decision.reason in ("context_match", "context_entity_rescue")
    assert 1 in decision.matched_sections


def test_context_match_zhongshan_via_search_content():
    context = build_report_entity_context(
        "# 广州旅游",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://src/page",
                            "title": "中山纪念堂位于广州，是广州近代著名建筑。",
                            "content": "中山纪念堂位于广州，是广州近代著名建筑。",
                        }
                    ]
                }
            ]
        },
        query="广州旅游",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert decision.reason in ("context_match", "context_entity_rescue")


# ---------------------------------------------------------------------------
# Step 4 — context rescue (source URL not in mapped sources)
# ---------------------------------------------------------------------------


def test_context_entity_rescues_source_mapping_miss():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔位于广州。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(
        candidate("广州塔珠江夜景", "https://unmapped.example/photo"),
        context,
    )
    assert decision.status == "keep"
    assert decision.reason == "context_entity_rescue"


# ---------------------------------------------------------------------------
# Step 5 — unresolved / unrelated entity
# ---------------------------------------------------------------------------


def test_unrelated_church_in_guangzhou_report_is_unresolved():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州旅游",
    )
    decision = evaluate_candidate(candidate("Church of St. Anthony"), context)
    assert decision.status == "drop"
    assert decision.reason in ("foreign_entity_conflict", "unresolved_entity_relation")


def test_unresolved_entity_relation():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate("某地中山纪念堂"), context)
    assert decision.status == "drop"
    assert decision.reason == "unresolved_entity_relation"


# ---------------------------------------------------------------------------
# Step 6 — missing alt
# ---------------------------------------------------------------------------


def test_missing_alt_is_dropped():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate(""), context)
    assert decision.status == "drop"
    assert decision.reason == "missing_alt"


def test_whitespace_only_alt_is_dropped():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate("   "), context)
    assert decision.status == "drop"
    assert decision.reason == "missing_alt"


# ---------------------------------------------------------------------------
# From the brief's original failing tests
# ---------------------------------------------------------------------------


def test_named_entity_confirmed_by_section_is_kept():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 中山纪念堂\n中山纪念堂位于广州。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert 1 in decision.matched_sections


def test_named_entity_without_current_context_is_rejected():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate("某地中山纪念堂"), context)
    assert decision.status == "drop"
    assert decision.reason == "unresolved_entity_relation"


# ---------------------------------------------------------------------------
# Sanity checks on the dataclass surface
# ---------------------------------------------------------------------------


def test_decision_is_dataclass_with_required_fields():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate("广州塔"), context)
    assert decision.url == "https://img.example/a.jpg"
    assert decision.status in ("keep", "drop")
    assert isinstance(decision.reason, str)
    assert isinstance(decision.entities, frozenset)
    assert isinstance(decision.matched_sections, frozenset)
    assert decision.source_signal in ("strong", "weak", "none")
    assert isinstance(decision.evidence_refs, tuple)


def test_context_build_raises_for_non_string_markdown():
    from local_deep_research.images.relevance import ContextBuildFailed
    with pytest.raises(ContextBuildFailed):
        build_report_entity_context(None, {"findings": []})
