"""Tests for the 2026-08-29 subsection-repetition fix (A+B+C).

Root cause (research e14f3600, 第十四世达赖喇嘛丹增嘉措): the synthesis
LLM copied prior subsection bodies verbatim — 3x identical bodies under
variant headings — after seeing them in the CONTENT ALREADY WRITTEN
context.

- Fix A: ``_dedupe_repeated_subsections`` (hash-only, preserves first
  occurrence and its citations).
- Fix B: ``_build_previous_context`` summarizes findings instead of
  including full bodies.
- Fix C: ``_count_duplicate_subsection_blocks`` mirrors A's verdict in
  the [CITE-INLINE] self_check log.
"""

import re
from unittest.mock import MagicMock

import pytest

from local_deep_research.citation_handlers.standard_citation_handler import (
    _count_duplicate_subsection_blocks,
)
from local_deep_research.report_generator import IntegratedReportGenerator


@pytest.fixture
def generator():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    gen.max_context_sections = 3
    gen.max_context_chars = 4000
    return gen


# Realistic Chinese body long enough to clear the 40-char floor.
_BODY_A = (
    "第十三世达赖喇嘛土登嘉措于 1933 年圆寂，当时中国正处于军阀混战时期，"
    "西藏内部政治环境复杂。这一时期的转世灵童认定过程受到多方政治势力的介入，"
    "摄政热振活佛主导了寻访工作，并派出多路寻访队伍前往青海、西康等地考察灵童候选。"
)
_BODY_A_ALT_CITES = re.sub(r"\[\[2\]\]", "[[54]]", _BODY_A)
_BODY_B = (
    "第十四世达赖喇嘛的寻访队伍于 1936 年抵达青海湟中县，在当地确认了拉莫顿珠为"
    "候选灵童之一。寻访队伍观察到灵童对前世遗物的辨认表现，这一认定过程随后经过"
    "西藏地方政府的核准程序，并由摄政上报国民政府请求免于金瓶掣签。"
)


def _dup_content():
    return (
        f"### 第十三世达赖喇嘛圆寂后的特殊历史背景\n\n{_BODY_A} [[2]]\n\n"
        f"### 第十四世达赖喇嘛的寻访与认定\n\n{_BODY_B} [[9]]\n\n"
        f"### 第十三世达赖喇嘛圆寂后的特殊历史背景\n\n{_BODY_A} [[2]]\n\n"
        f"### 历史背景再述\n\n{_BODY_A_ALT_CITES}\n\n"
    )


class TestDedupeRepeatedSubsections:
    def test_verbatim_copies_dropped_first_kept(self, generator):
        result = generator._dedupe_repeated_subsections(_dup_content())
        assert result.count(_BODY_A) == 1

    def test_renumbered_citations_still_detected(self, generator):
        # Third copy differs only by citation number — must still drop.
        result = generator._dedupe_repeated_subsections(_dup_content())
        assert "历史背景再述" not in result

    def test_unique_sections_untouched(self, generator):
        content = f"### 标题一\n\n{_BODY_A}\n\n### 标题二\n\n{_BODY_B}\n\n"
        assert generator._dedupe_repeated_subsections(content) == content

    def test_first_occurrence_citations_preserved(self, generator):
        result = generator._dedupe_repeated_subsections(_dup_content())
        # The kept copy retains its own citation marker.
        assert "[[2]]" in result
        # The unique section's marker survives untouched.
        assert "[[9]]" in result

    def test_short_blocks_never_deduped(self, generator):
        # Two identical short lines — below the 40-char floor, kept.
        content = "## A\n\n短句。\n\n## B\n\n短句。\n\n"
        assert generator._dedupe_repeated_subsections(content) == content

    def test_content_without_headings_unchanged(self, generator):
        content = f"{_BODY_A} [[2]]\n\n{_BODY_A} [[2]]\n\n"
        # No ## headings -> no block structure -> passthrough (dedup
        # happens at block granularity only).
        assert generator._dedupe_repeated_subsections(content) == content

    def test_empty_and_single_block_passthrough(self, generator):
        assert generator._dedupe_repeated_subsections("") == ""
        single = f"## Only\n\n{_BODY_A}\n\n"
        assert generator._dedupe_repeated_subsections(single) == single


class TestBuildPreviousContextSummarized:
    def test_long_body_capped_to_excerpt(self, generator):
        # Body longer than the excerpt budget must appear truncated,
        # never in full.
        long_body = _BODY_A * 3
        finding = f"[章节 > 小节]\n## 小节标题\n\n{long_body} [[2]]\n\n"
        result = generator._build_previous_context([finding])
        assert long_body not in result
        assert "CONTENT ALREADY WRITTEN" in result

    def test_tag_and_headings_and_excerpt_present(self, generator):
        finding = f"[章节 > 小节]\n## 小节标题\n\n{_BODY_A}\n\n"
        result = generator._build_previous_context([finding])
        assert "[章节 > 小节]" in result
        assert "小节标题" in result
        assert "Excerpt:" in result

    def test_excerpt_capped(self, generator):
        long_para = "字" * 2000
        finding = f"[章节 > 小节]\n## 标题\n\n{long_para}\n\n"
        result = generator._build_previous_context([finding])
        excerpt = re.search(r"Excerpt: (.*?)…\n", result, re.DOTALL)
        assert excerpt is not None
        assert len(excerpt.group(1)) <= generator._CONTEXT_EXCERPT_CHARS

    def test_empty_findings_returns_empty(self, generator):
        assert generator._build_previous_context([]) == ""


class TestCountDuplicateSubsectionBlocks:
    def test_mirrors_dedup_verdict(self):
        assert _count_duplicate_subsection_blocks(_dup_content()) == 2

    def test_clean_body_zero(self):
        content = f"### 标题一\n\n{_BODY_A}\n\n### 标题二\n\n{_BODY_B}\n\n"
        assert _count_duplicate_subsection_blocks(content) == 0

    def test_no_headings_zero(self):
        assert _count_duplicate_subsection_blocks(f"{_BODY_A}\n\n{_BODY_A}\n\n") == 0
