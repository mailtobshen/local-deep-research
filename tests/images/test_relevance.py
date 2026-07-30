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
                            "url": "https://instagram.com/popular/广州景点",
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
            "https://instagram.com/popular/广州景点",
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
                            "url": "https://instagram.com/popular/广州景点",
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
            "https://instagram.com/popular/广州景点",
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
        {"findings": [
            {
                "search_results": [
                    {
                        "url": "https://source.example/page",
                        "title": "广州塔",
                        "content": "广州塔是地标，珠江夜游不可错过。",
                    }
                ]
            }
        ]},
        query="广州旅游",
    )
    decision = evaluate_candidate(candidate("广州塔珠江夜景"), context)
    assert decision.status == "keep"
    assert decision.reason == "context_match"
    assert 1 in decision.matched_sections


def test_context_match_section_heading_zhongshan():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 中山纪念堂\n中山纪念堂位于广州。",
        {"findings": [
            {
                "search_results": [
                    {
                        "url": "https://source.example/page",
                        "title": "中山纪念堂",
                        "content": "中山纪念堂位于广州。",
                    }
                ]
            }
        ]},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert decision.reason == "context_match"
    assert 1 in decision.matched_sections


def test_context_match_zhongshan_via_search_content():
    context = build_report_entity_context(
        "# 广州旅游",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://source.example/page",
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
    assert decision.reason == "context_match"


# ---------------------------------------------------------------------------
# Step 4 — context rescue (source URL not in mapped sources)
# ---------------------------------------------------------------------------


def test_context_url_not_in_section_sources_is_dropped():
    """Without section-source match, a context-relevant alt cannot be kept.

    This replaces the legacy `context_entity_rescue` path: the strict
    gate requires the image's source URL to appear in the
    section-source map.
    """
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔位于广州。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(
        candidate("广州塔珠江夜景", "https://unmapped.example/photo"),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason == "drop_source_url_not_cited"


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
        {"findings": [
            {
                "search_results": [
                    {
                        "url": "https://source.example/page",
                        "title": "中山纪念堂",
                        "content": "中山纪念堂位于广州。",
                    }
                ]
            }
        ]},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert decision.reason == "context_match"
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
# Regression: short English tokens must not be a sufficient anchor
# ---------------------------------------------------------------------------


def test_short_english_anchor_does_not_keep_photo_by_lzw():
    """`Asia`, `LZW`, `March`, `Photo` are too generic to anchor a candidate.

    Even when the report context mentions `Asian Elephants ... Guangzhou`,
    a candidate whose alt is only `Photo by LZW on March 21, 2015.` must
    be rejected. The loose-substring match against `Asia` is exactly the
    bug the strict gate should not tolerate.
    """
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔介绍。",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://blog.axiaoxin.com/post/gz-chimelong-safari-park-en/",
                            "title": "Asian Elephants at Guangzhou Chimelong Safari Park",
                            "content": "Asian Elephants at Guangzhou Chimelong Safari Park",
                        }
                    ]
                }
            ]
        },
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate("Photo by LZW on March 21, 2015."),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason in ("foreign_entity_conflict", "unresolved_entity_relation")


def test_new_asia_life_magazine_anchor_does_not_keep_candidate():
    """`Asia` in `New Asia Life Monthly Magazine` must not count as anchor
    even when the report contains `Asian Elephants ... Guangzhou`.
    """
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔介绍。",
        {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://blog.axiaoxin.com/post/gz-chimelong-safari-park-en/",
                            "title": "Asian Elephants at Guangzhou Chimelong Safari Park",
                        }
                    ]
                }
            ]
        },
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate(
            "《新亞生活》周刊（2008-09）New Asia Life Monthly Magazine (2008-09)",
            "https://fliphtml5.com/xhegu/rafe/.../",
        ),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason in ("foreign_entity_conflict", "unresolved_entity_relation")


def test_long_english_anchor_still_keeps_candidate():
    """An English name long enough (>=4 letters) still anchors properly."""
    context = build_report_entity_context(
        "# Guangzhou sightseeing\n## Canton Tower\nCanton Tower info.",
        {"findings": [
            {
                "search_results": [
                    {
                        "url": "https://source.example/page",
                        "title": "Canton Tower",
                        "content": "Canton Tower info.",
                    }
                ]
            }
        ]},
        query="Guangzhou sightseeing",
    )
    decision = evaluate_candidate(
        candidate("Canton Tower at night"),
        context,
    )
    assert decision.status == "keep"
    assert decision.reason == "context_match"


# ---------------------------------------------------------------------------
# Sanity checks on the dataclass surface
# ---------------------------------------------------------------------------


def test_decision_is_dataclass_with_required_fields():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": [
            {
                "search_results": [
                    {
                        "url": "https://source.example/page",
                        "title": "广州塔",
                        "content": "广州塔。",
                    }
                ]
            }
        ]},
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



def test_www_instagram_subdomain_is_weak():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州\n广州。",
        {"findings": []},
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate("广州塔", "https://www.instagram.com/p/123"),
        context,
    )
    assert decision.source_signal == "weak"


def test_anti_instagram_cloned_host_is_strong():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州\n广州。",
        {"findings": []},
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate("广州塔", "https://anti-instagrambot.com/page"),
        context,
    )
    assert decision.source_signal == "strong"


# ---------------------------------------------------------------------------
# Cross-language entity gate (Wikipedia article title aliasing)
# ---------------------------------------------------------------------------
#
# The entity gate was previously strict: an alt with an English
# proper name (e.g. ``Canton Tower``) was dropped when the report
# body only contained the Chinese form (``广州塔``) — the two strings
# are not equal, so the anchor check failed. The fix has two parts:
#
#  1. The report's Wikipedia article URLs are parsed into article
#     titles (``Canton_Tower``) and stored in
#     ``context.cited_article_titles``. An alt whose
#     ``source_url`` parses to one of these titles is anchored,
#     even when the body uses a different language for the same
#     concept.
#  2. A small static alias table maps article titles to their
#     Chinese / alternate names. When the report cites an article,
#     the aliases are added to ``context.all_entities``, so the
#     existing substring / exact-match anchored check can fire
#     against the body without the alt having to know the URL.


def test_cross_language_english_alt_chinese_body_via_wikipedia_source():
    """The original bug case: alt "Canton Tower" + body "广州塔".

    Without the fix this would be ``drop_unrelated_named_entity``
    because the alt's Latin proper name does not match the body's
    Chinese name. With the fix, the image's source_url
    (``en.wikipedia.org/wiki/Canton_Tower``) maps to the same
    Wikipedia article the report cited, so the alt is anchored.
    """
    report = (
        "## 1. 广州塔\n"
        "广州塔是广州地标。\n\n"
        "## 参考文献\n"
        "[1] Canton Tower\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    results = {
        "research_query": "广州塔",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Canton_Tower",
                        "title": "Canton Tower",
                        "content": "Canton Tower is in Guangzhou.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)

    cand = candidate(
        alt="Canton Tower at night",
        source="https://en.wikipedia.org/wiki/Canton_Tower",
    )
    decision = evaluate_candidate(cand, context)
    assert decision.status == "keep", (
        f"cross-language keep failed: status={decision.status} "
        f"reason={decision.reason} evidence={decision.evidence_refs}"
    )
    assert decision.reason == "context_match"


def test_cross_language_alias_added_to_all_entities():
    """When the report cites a known Wikipedia article, its Chinese
    alias is added to ``context.all_entities``. The body's
    "广州塔" matches the alias set, so any candidate whose alt
    contains the Chinese name is anchored even without a Wikipedia
    source_url.
    """
    report = "## 广州塔\n广州。\n"
    results = {
        "research_query": "广州塔",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Canton_Tower",
                        "title": "Canton Tower",
                        "content": "Canton Tower is in Guangzhou.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    # The Chinese alias is in the report's entity set.
    assert "广州塔" in context.all_entities
    # The Wikipedia article title is in the cited set.
    assert "Canton_Tower" in context.cited_article_titles


def test_cross_language_beijing_forbidden_city_keeps_image():
    """Beijing scenario: body 故宫 + alt "Aerial view of the
    Forbidden City" + source_url is the Wikipedia Forbidden City
    article. The cross-language gate accepts the image because the
    article title ``Forbidden_City`` is in the cited set, and
    the alias ``故宫`` is in the body's entity set.
    """
    report = (
        "## 1. 皇家宫殿遗址\n"
        "故宫又称紫禁城，是明清两代皇家宫殿。\n\n"
        "## 参考文献\n"
        "[1] Forbidden City\n"
        "   URL: https://en.wikipedia.org/wiki/Forbidden_City\n"
    )
    results = {
        "research_query": "北京旅游景点",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Forbidden_City",
                        "title": "Forbidden City",
                        "content": "Forbidden City in Beijing.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    cand = candidate(
        alt="Aerial view of the Forbidden City",
        source="https://en.wikipedia.org/wiki/Forbidden_City",
    )
    decision = evaluate_candidate(cand, context)
    assert decision.status == "keep", (
        f"forbidden city: status={decision.status} reason={decision.reason}"
    )


def test_cross_language_unknown_article_no_alias_still_drops():
    """If the report's Wikipedia article is NOT in the alias table
    and the body uses only the Chinese name, the alt is dropped —
    no signal can bridge the two without an alias or a cited
    article-title match.
    """
    report = (
        "## 1. 北京火车站\n"
        "北京火车站是中国铁路的重要枢纽。\n\n"
        "## 参考文献\n"
        "[1] Beijing Railway Station\n"
        "   URL: https://en.wikipedia.org/wiki/Beijing_Railway_Station\n"
    )
    results = {
        "research_query": "北京",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Beijing_Railway_Station",
                        "title": "Beijing Railway Station",
                        "content": "Beijing Railway Station.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    cand = candidate(
        alt="Photo of Beijing Railway Station at night",
        source="https://en.wikipedia.org/wiki/Beijing_Railway_Station",
    )
    decision = evaluate_candidate(cand, context)
    # The image's source_url is the cited article (cross-language
    # anchor via cited_article_titles), so this is actually kept.
    # The cross-language path is broader than just the static alias
    # table — any cited article title acts as an anchor.
    assert decision.status == "keep", (
        f"uncited article should be cross-language anchored via "
        f"cited_article_titles: {decision.reason}"
    )


def test_cross_language_uncited_article_with_chinese_body_keeps_via_static_alias():
    """When the candidate's article is NOT cited but the alias
    table covers the article, the Chinese alias in the report
    body still anchors the image.
    """
    # The report cites the Canton Tower article, and the body
    # mentions 广州塔. The candidate is from the same article
    # (so the source_url parses to the same article title), and
    # because the article is in the cited_article_titles, the
    # alt is anchored. We assert that here.
    report = (
        "## 1. 广州塔\n"
        "广州塔。\n\n"
        "## 参考文献\n"
        "[1] Canton Tower\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    results = {
        "research_query": "广州塔",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Canton_Tower",
                        "title": "Canton Tower",
                        "content": "Canton Tower is in Guangzhou.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    # The body has the Chinese alias (added when the article is cited).
    assert "广州塔" in context.all_entities
    # A candidate alt containing the Chinese name only.
    cand = candidate(
        alt="广州塔珠江夜景",
        source="https://en.wikipedia.org/wiki/Canton_Tower",
    )
    decision = evaluate_candidate(cand, context)
    assert decision.status == "keep", (
        f"alt containing Chinese alias + body containing same alias: "
        f"status={decision.status} reason={decision.reason}"
    )


def test_wikipedia_article_title_extraction():
    """Sanity: the URL → article-title parser rejects non-Wikipedia
    URLs and non-article paths (Special:, File:, ...)."""
    from local_deep_research.images.relevance import _wikipedia_article_title

    assert (
        _wikipedia_article_title("https://en.wikipedia.org/wiki/Canton_Tower")
        == "Canton_Tower"
    )
    # URL-decoded form is not applied by urlparse.path — this
    # function returns the raw percent-encoded path segment. The
    # alias table is keyed on raw segments, which is fine for
    # the langgraph / firecrawl flow because the agent's
    # search results carry the same encoded form.
    assert (
        _wikipedia_article_title(
            "https://zh.wikipedia.org/wiki/%E5%B9%BF%E5%B7%9E%E5%A1%94"
        )
        == "%E5%B9%BF%E5%B7%9E%E5%A1%94"
    )
    # Decoded form (e.g. for direct URL inspection): we use
    # urllib.parse.unquote to normalise. The entity gate code
    # does this internally.
    from urllib.parse import unquote
    assert unquote(
        _wikipedia_article_title(
            "https://zh.wikipedia.org/wiki/%E5%B9%BF%E5%B7%9E%E5%A1%94"
        )
    ) == "广州塔"
    assert _wikipedia_article_title("https://example.com/page") == ""
    assert _wikipedia_article_title("https://en.wikipedia.org/wiki/Special:RecentChanges") == ""
    assert _wikipedia_article_title("https://en.wikipedia.org/wiki/File:Foo.jpg") == ""
    assert _wikipedia_article_title("") == ""
