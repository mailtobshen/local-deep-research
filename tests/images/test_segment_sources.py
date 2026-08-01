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


def test_extract_segment_sources_orphan_section_has_no_urls():
    """An orphan section with no inline citation gets an empty URL list.

    It does NOT inherit the previous section's URLs — a section without
    its own citation has no authoritative source and should not receive
    images. (Inheritance was removed: it let fabricated sources leak
    into image placement.)"""
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
    assert out[1][2] == []  # orphan — no inheritance
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


# ---- CITE_LIST_ROW_RE: end-to-end URL parsing across many shapes ----
#
# These cases exist because the previous regex silently swallowed
# the *next* row's title when a row's URL: line was empty, polluting
# the citation map. Each test feeds a small References block and
# asserts the resulting num→url map. The cases cover:
#  * the original bug (empty URL sandwiched between valid rows)
#  * empty URL at the very end of the block
#  * URLs that span multiple lines via trailing slashes / no
#    trailing slash
#  * CJK characters in titles
#  * comma-group citation numbers [1, 2] URL: ...
#  * URLs with query strings, fragments, ports, userinfo
#  * the regex being applied to Sources generated by the
#    production format_links_to_markdown output, which uses
#    canonical URL keys (lowercased host, no trailing slash,
#    no utm params).

from local_deep_research.text_optimization.citation_formatter import (
    CITE_LIST_ROW_RE,
)
from local_deep_research.images.relevance import (
    _scan_references_block,
    extract_segment_sources,
)


# Re-export the real scanner under a short name for the per-row tests.
_parse_sources = _scan_references_block


def test_references_empty_url_does_not_swallow_next_row_title():
    """The original bug: [6] with an empty URL: line, followed by
    [7] with a real URL. The previous regex captured '[7] Hutong'
    as [6]'s URL. The fix must drop the empty-URL row and map [7]
    correctly.
    """
    md = (
        "## 参考文献\n\n"
        "[1] Foo\n"
        "   URL: https://example.com/a\n"
        "[6] Beijing opera\n"
        "   URL:\n"
        "[7] Hutong\n"
        "   URL: https://en.wikipedia.org/wiki/Hutong\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://example.com/a",
        "7": "https://en.wikipedia.org/wiki/Hutong",
    }, f"empty-URL row poisoned the map: {parsed}"


def test_references_empty_url_at_end_of_block():
    """An empty URL: line at the end of the block (no following
    [N] row) — must be dropped cleanly."""
    md = (
        "## References\n\n"
        "[1] A\n"
        "   URL: https://example.com/a\n"
        "[2] B with no URL\n"
        "   URL:\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {"1": "https://example.com/a"}


def test_references_empty_url_as_only_row():
    """Single row, empty URL."""
    md = "## 参考文献\n\n[1] A\n   URL:\n"
    assert _parse_sources(md) == {}


def test_references_comma_group_with_url():
    """``[1, 2] URL: https://...`` produces entries for both 1
    and 2 pointing to the same URL."""
    md = (
        "## References\n\n"
        "[1, 2] Chen Clan and Shamian\n"
        "   URL: https://en.wikipedia.org/wiki/Guangzhou\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://en.wikipedia.org/wiki/Guangzhou",
        "2": "https://en.wikipedia.org/wiki/Guangzhou",
    }


def test_references_url_with_query_string_and_fragment():
    """URLs from real search engines carry utm params and
    fragments. They must be preserved verbatim — the parser is
    not allowed to normalise."""
    md = (
        "## Sources\n\n"
        "[1] Foo\n"
        "   URL: https://www.example.com/page?utm_source=x&id=42#section\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://www.example.com/page?utm_source=x&id=42#section",
    }


def test_references_url_with_port_and_userinfo():
    """URLs with explicit ports and userinfo are common in source
    metadata."""
    md = (
        "## Sources\n\n"
        "[1] Foo\n"
        "   URL: https://user:pass@api.example.com:8443/v1/resource\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://user:pass@api.example.com:8443/v1/resource",
    }


def test_references_cjk_titles_with_unicode_url():
    """CJK characters in the title (常见于 format_links_to_markdown
    output for Chinese sources) must not break the parser."""
    md = (
        "## 参考文献\n\n"
        "[1] 长城 — 维基百科\n"
        "   URL: https://zh.wikipedia.org/wiki/长城\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {"1": "https://zh.wikipedia.org/wiki/长城"}


def test_references_three_row_block_with_middle_empty_url():
    """Three rows where the middle one is empty-URL. The third
    row must still resolve correctly even though the previous
    failed-to-parse row used to leak into the next match."""
    md = (
        "## Sources\n\n"
        "[1] First\n"
        "   URL: https://a.com\n"
        "[2] Empty\n"
        "   URL:\n"
        "[3] Third\n"
        "   URL: https://c.com\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://a.com",
        "3": "https://c.com",
    }


def test_references_real_format_links_to_markdown_output():
    """The output shape produced by format_links_to_markdown
    (which langgraph actually writes to the report) includes
    a 'source nr:' annotation. The parser must still extract
    the URL correctly from this richer format."""
    md = (
        "## Sources\n\n"
        "[1] Beijing — Wikipedia (source nr: 1)\n"
        "   URL: https://en.wikipedia.org/wiki/Beijing\n"
        "\n"
        "[2, 3] Forbidden City (source nr: 2, 3)\n"
        "   URL: https://en.wikipedia.org/wiki/Forbidden_City\n"
    )
    parsed = _parse_sources(md)
    assert parsed == {
        "1": "https://en.wikipedia.org/wiki/Beijing",
        "2": "https://en.wikipedia.org/wiki/Forbidden_City",
        "3": "https://en.wikipedia.org/wiki/Forbidden_City",
    }


def test_extract_segment_sources_full_markdown_with_empty_url_row():
    """End-to-end: a full Beijing-like markdown where one row's
    URL: line is blank. The section that cited that unresolvable
    number gets an empty URL list (no inheritance), and crucially
    does not pollute itself with a '[N] Title' literal from the
    next row.
    """
    md = (
        "## 1. Section A\n"
        "Body [1].\n\n"
        "## 2. Section B\n"
        "Body [2].\n\n"
        "## 3. Section C\n"
        "Body [3].\n\n"
        "## 参考文献\n\n"
        "[1] A\n"
        "   URL: https://a.com\n"
        "[2] B with no URL\n"
        "   URL:\n"
        "[3] C\n"
        "   URL: https://c.com\n"
    )
    sections = extract_segment_sources(md, results={})
    # Section 1 (A) → [a.com]
    assert sections[0][2] == ["https://a.com"]
    # Section 2 (B) cites [2] which has no URL → empty list, no
    # inheritance from section 1.
    assert sections[1][2] == [], (
        f"section 2 has no resolvable source, got {sections[1][2]}"
    )
    # Section 3 (C) → [c.com]. Importantly NOT '[2] B with no URL'
    # (the previous bug would have polluted this).
    assert sections[2][2] == ["https://c.com"], (
        f"section 3 must show c.com, not a leaked title: {sections[2][2]}"
    )
