"""Tests for extract_segment_sources — Markdown-references path only.

After the 2026-07-30 refactor (see plan
`docs/superpowers/plans/2026-07-29-image-enhancer-early-skip-and-title-weighting.md`),
``extract_segment_sources`` resolves per-section URLs by mapping
``[N]`` markers in each section's body to entries in the trailing
References/Sources/参考文献 list. The previous search_results
candidate path was removed because every strategy in this repo writes
``"findings": []`` (verified across all strategies in
``src/local_deep_research/advanced_search_system/strategies/``).
"""

from __future__ import annotations

from local_deep_research.images.postprocessing import extract_segment_sources


def test_extract_segment_sources_from_markdown_references():
    """Happy path: ``[1]`` in section body resolves to URL in trailing list."""
    md = (
        "## 摘要\n\n"
        "无引用。\n\n"
        "## 1. 广州塔（Canton Tower）\n\n"
        "广州塔是广州地标。详见 [1]。\n\n"
        "## 参考文献\n"
        "[1] Canton Tower — Wikipedia\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    out = extract_segment_sources(md, results={})
    # 3 sections: 摘要, 广州塔, 参考文献
    assert len(out) == 3
    assert out[0][0] == "摘要"
    assert out[0][2] == []
    assert out[1][0] == "1. 广州塔（Canton Tower）"
    assert out[1][2] == ["https://en.wikipedia.org/wiki/Canton_Tower"]
    # The References section itself collects its own URLs; downstream
    # is_skipped_section_heading() suppresses insertion there.
    assert out[2][0] == "参考文献"
    assert out[2][2] == ["https://en.wikipedia.org/wiki/Canton_Tower"]


def test_extract_segment_sources_inherits_when_no_inline_citation():
    """Orphan sections inherit the previous section's URL list."""
    md = (
        "## A\n\n引用 [1] 见下。\n\n"
        "## B\n\n本节无引用。\n\n"
        "## C\n\n引用 [2]。\n\n"
        "## References\n"
        "[1] A source\n"
        "   URL: https://example.com/a\n"
        "[2] C source\n"
        "   URL: https://example.com/c\n"
    )
    out = extract_segment_sources(md, results={})
    assert out[0][2] == ["https://example.com/a"]
    assert out[1][2] == ["https://example.com/a"]  # inherited
    assert out[2][2] == ["https://example.com/c"]


def test_extract_segment_sources_no_references_section_returns_empty():
    """No trailing References heading → all sections get empty URL lists."""
    md = (
        "## A\n\n引用 [1] 但没有参考文献段。\n\n"
        "## B\n\n也没有。\n"
    )
    out = extract_segment_sources(md, results={})
    assert out[0][2] == []
    assert out[1][2] == []


def test_extract_segment_sources_dedup_repeated_citation_in_body():
    """``[1][1][1]`` in one body → 1 URL entry, not 3."""
    md = (
        "## A\n\n"
        "广州塔。详见 [1][1][1] 和同样 [1]。\n\n"
        "## References\n"
        "[1] Canton Tower\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    out = extract_segment_sources(md, results={})
    assert out[0][2] == ["https://en.wikipedia.org/wiki/Canton_Tower"]


def test_extract_segment_sources_dedup_same_url_from_different_numbers():
    """Two citation numbers that resolve to the same URL collapse to one entry."""
    md = (
        "## A\n\n引用 [1] 和 [2]。\n\n"
        "## References\n"
        "[1] Canton Tower\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
        "[2] Canton Tower (alternate listing)\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    out = extract_segment_sources(md, results={})
    # Layer-2 dedup: the same URL appears once, not twice.
    assert out[0][2] == ["https://en.wikipedia.org/wiki/Canton_Tower"]


def test_extract_segment_sources_comma_group_resolves_to_each_number():
    """``[2, 3]`` comma group → both URLs flow into the section's list."""
    md = (
        "## A\n\n引用 [2, 3]。\n\n"
        "## References\n"
        "[2] Chen Clan\n"
        "   URL: https://en.wikipedia.org/wiki/Chen_Clan_Ancestral_Hall\n"
        "[3] Shamian\n"
        "   URL: https://en.wikipedia.org/wiki/Shamian\n"
    )
    out = extract_segment_sources(md, results={})
    # Order of citations within a section is not guaranteed (set-driven
    # collection), so assert membership not equality.
    assert set(out[0][2]) == {
        "https://en.wikipedia.org/wiki/Chen_Clan_Ancestral_Hall",
        "https://en.wikipedia.org/wiki/Shamian",
    }


def test_extract_segment_sources_skips_url_less_reference_rows():
    """A reference row without a ``URL:`` line is ignored — the section
    cannot cite a URL it does not know about."""
    md = (
        "## A\n\n引用 [1] 和 [2]。\n\n"
        "## References\n"
        "[1] Has URL\n"
        "   URL: https://example.com/has\n"
        "[2] No URL here, just a title\n"
    )
    out = extract_segment_sources(md, results={})
    # Only [1]'s URL flows in; [2] is unresolvable so it contributes nothing.
    assert out[0][2] == ["https://example.com/has"]


def test_extract_segment_sources_results_parameter_is_ignored():
    """The ``results`` parameter is kept for API compatibility but is
    not consulted. Passing search_results-style data must not affect the
    output."""
    md = (
        "## A\n\n引用 [1]。\n\n"
        "## References\n"
        "[1] A\n"
        "   URL: https://example.com/a\n"
    )
    noisy_results = {
        "findings": [
            {"search_results": [
                {"link": "https://wrong.com/should-not-appear",
                 "title": "Wrong Source", "content": "Wrong", "snippet": ""},
            ]}
        ]
    }
    out = extract_segment_sources(md, results=noisy_results)
    assert out[0][2] == ["https://example.com/a"]


def test_extract_segment_sources_english_sources_heading():
    """English ``## Sources`` heading also triggers References detection."""
    md = (
        "## A\n\n引用 [1]。\n\n"
        "## Sources\n"
        "[1] A\n"
        "   URL: https://example.com/a\n"
    )
    out = extract_segment_sources(md, results={})
    assert out[0][2] == ["https://example.com/a"]


def test_extract_segment_sources_no_heading_returns_implicit_section():
    """A document with no ``##`` headings is one implicit section."""
    md = "Body without any headings. 引用 [1] 但是没有参考文献段。"
    out = extract_segment_sources(md, results={})
    assert len(out) == 1
    assert out[0][0] == ""  # implicit empty heading
    assert out[0][2] == []  # no References → empty


def test_extract_segment_sources_empty_markdown_returns_empty_list():
    """Empty input → empty list (the drift guard in postprocessing checks len == 0)."""
    out = extract_segment_sources("", results={})
    assert out == []
