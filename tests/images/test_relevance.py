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
# Cross-language entity gate (Wikipedia URL is the canonical anchor)
# ---------------------------------------------------------------------------
#
# The entity gate was previously strict: an alt with an English
# proper name (e.g. ``Canton Tower``) was dropped when the report
# body only contained the Chinese form (``广州塔``) — the two strings
# are not equal, so the anchor check failed.
#
# The fix uses the Wikipedia URL itself as the cross-language
# bridge. Every Wikipedia article, in any language, has the same
# concept as the same article in any other language. So:
#
#   1. The report's Wikipedia article URLs are parsed into article
#      titles (``Canton_Tower``) and stored in
#      ``context.cited_article_titles``. The spaced form
#      (``Canton Tower``) is also added to ``context.all_entities``
#      / ``primary`` so the existing substring anchor can also
#      fire when the alt contains the natural English name.
#   2. An alt whose ``source_url`` parses to a title in
#      ``cited_article_titles`` is anchored, regardless of body
#      language. This is the primary mechanism.
#   3. Short-acronym alts (DNA, USB) that pass the cross-language
#      check but fail the body-derived entity check still
#      ``unresolved_entity_relation``-drop. A second-stage
#      fallback expands the section match to all sections when
#      the candidate's article is in the cited set — the report
#      explicitly cited this article, so any section is a valid
#      home.
#
# A static per-domain alias table was tried first and removed:
# it cannot scale to research topics the table's author has not
# anticipated. The URL title is the only cross-language mechanism.


def test_cross_language_english_alt_chinese_body_via_wikipedia_source():
    """The original bug case: alt "Canton Tower" + body "广州塔".

    The image's source_url (``en.wikipedia.org/wiki/Canton_Tower``)
    is parsed to article title ``Canton_Tower``; the report
    cited the same article in its References block. The
    cross-language path anchors the alt.
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


def test_cross_language_cited_article_title_added_to_all_entities():
    """The cited article's title (with underscores replaced by
    spaces) is added to ``context.all_entities`` and ``primary``,
    so the existing exact/substring anchor can also fire for alts
    that contain the natural English name.
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
    assert "Canton_Tower" in context.cited_article_titles
    assert "Canton Tower" in context.all_entities  # spaced form
    assert "Canton Tower" in context.primary_entities


def test_cross_language_beijing_forbidden_city_keeps_image():
    """Beijing scenario: body 故宫 + alt "Aerial view of the
    Forbidden City" + source_url is the Wikipedia Forbidden City
    article. The cross-language path anchors via cited_article_titles.
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


def test_cross_language_any_uncited_article_still_keeps_via_cited_set():
    """The cross-language path uses the report's cited article
    titles as the anchor set, NOT a per-domain alias table.
    Any article title in ``cited_article_titles`` works —
    tourism, medical, legal, physics, etc.
    """
    report = (
        "## 1. 癌症\n"
        "癌症介绍。\n\n"
        "## 参考文献\n"
        "[1] Cancer\n"
        "   URL: https://en.wikipedia.org/wiki/Cancer\n"
    )
    results = {
        "research_query": "癌症",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Cancer",
                        "title": "Cancer",
                        "content": "Cancer is a disease.",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    cand = candidate(
        alt="Cancer cell under microscope",
        source="https://en.wikipedia.org/wiki/Cancer",
    )
    decision = evaluate_candidate(cand, context)
    assert decision.status == "keep"


def test_cross_language_short_acronym_dna_usb_keeps():
    """DNA and USB are 3-letter acronyms. The cross-language path
    anchors them via the cited article title even though the
    _is_substantial check would reject them as a regular
    substring match. This is a real-world case for scientific
    / technical reports.
    """
    for title, zh, alt in [
        ("DNA", "脱氧核糖核酸", "DNA double helix"),
        ("USB", "通用串行总线", "USB connector"),
    ]:
        report = (
            f"## 1. {zh}\n{zh}介绍。\n\n## 参考文献\n"
            f"[1] {title}\n   URL: https://en.wikipedia.org/wiki/{title}\n"
        )
        results = {
            "research_query": zh,
            "findings": [
                {
                    "search_results": [
                        {
                            "link": f"https://en.wikipedia.org/wiki/{title}",
                            "title": title,
                            "content": "",
                            "snippet": "",
                        }
                    ]
                }
            ],
        }
        context = build_report_entity_context(report, results)
        cand = candidate(
            alt=alt,
            source=f"https://en.wikipedia.org/wiki/{title}",
        )
        decision = evaluate_candidate(cand, context)
        assert decision.status == "keep", (
            f"{title}: status={decision.status} reason={decision.reason}"
        )


def test_cross_language_multi_word_article_keeps():
    """Multi-word article titles like "Quantum_entanglement" or
    "Machine_learning" pass through the cross-language path even
    when the alt uses the natural English form (with spaces,
    not underscores).
    """
    for title, zh, alt in [
        ("Quantum_entanglement", "量子纠缠", "Quantum entanglement diagram"),
        ("Machine_learning", "机器学习", "Neural network architecture"),
    ]:
        report = (
            f"## 1. {zh}\n{zh}介绍。\n\n## 参考文献\n"
            f"[1] {title}\n   URL: https://en.wikipedia.org/wiki/{title}\n"
        )
        results = {
            "research_query": zh,
            "findings": [
                {
                    "search_results": [
                        {
                            "link": f"https://en.wikipedia.org/wiki/{title}",
                            "title": title.replace("_", " "),
                            "content": "",
                            "snippet": "",
                        }
                    ]
                }
            ],
        }
        context = build_report_entity_context(report, results)
        cand = candidate(
            alt=alt,
            source=f"https://en.wikipedia.org/wiki/{title}",
        )
        decision = evaluate_candidate(cand, context)
        assert decision.status == "keep", (
            f"{title}: status={decision.status} reason={decision.reason}"
        )


def test_cross_language_source_url_must_be_cited():
    """Even with a perfect cross-language match on the alt
    (Canton Tower ↔ 广州塔), a candidate whose ``source_url``
    is NOT in the report's cited URL set is dropped with
    ``drop_source_url_not_cited``. The cross-language path
    cannot save candidates from the source-not-cited gate — the
    cited URL list is the contract, and a non-cited source means
    the LLM never asked for this image.
    """
    report = (
        "## 1. 广州塔\n"
        "广州塔。\n\n## 参考文献\n"
        "[1] Canton Tower\n   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    results = {
        "research_query": "广州塔",
        "findings": [
            {
                "search_results": [
                    {
                        "link": "https://en.wikipedia.org/wiki/Canton_Tower",
                        "title": "Canton Tower",
                        "content": "",
                        "snippet": "",
                    }
                ]
            }
        ],
    }
    context = build_report_entity_context(report, results)
    cand = candidate(
        alt="Canton Tower at night",
        source="https://a1.ctrip.com/photo/canton-tower",  # NOT cited
    )
    decision = evaluate_candidate(cand, context)
    assert decision.status == "drop"
    assert decision.reason == "drop_source_url_not_cited"


def test_wikipedia_article_title_extraction():
    """Sanity: the URL → article-title parser rejects non-Wikipedia
    URLs and non-article paths (Special:, File:, ...)."""
    from local_deep_research.images.relevance import _wikipedia_article_title

    assert (
        _wikipedia_article_title("https://en.wikipedia.org/wiki/Canton_Tower")
        == "Canton_Tower"
    )
    # Non-article paths are rejected so they don't pollute the
    # cited_article_titles set with non-content identifiers.
    assert _wikipedia_article_title("https://example.com/page") == ""
    assert _wikipedia_article_title("https://en.wikipedia.org/wiki/Special:RecentChanges") == ""
    assert _wikipedia_article_title("https://en.wikipedia.org/wiki/File:Foo.jpg") == ""
    assert _wikipedia_article_title("") == ""
    # Any *.wikipedia.org is accepted (en / zh / ja / de / ...).
    assert (
        _wikipedia_article_title(
            "https://zh.wikipedia.org/wiki/%E5%B9%BF%E5%B7%9E%E5%A1%94"
        )
        == "%E5%B9%BF%E5%B7%9E%E5%A1%94"
    )
