"""Numbered reference headings + empty rebuilt 参考文献 block.

Observed 2026-08-25 09:41 (research 496944b7, FPV穿越机):

1. The LLM wrote its references section under ``## 6. 参考资料`` —
   a NUMBERED heading. Both the English and CJK sources-section
   patterns require the heading text to start with the keyword
   (``参考文献`` / ``References`` / ...), so the optional ``6. ``
   list-number prefix made every pattern miss. The section survived
   strip/enforce verbatim and rendered in the exported report as a
   duplicate of the trailing ``## 参考文献``.

2. ``[CITE-ENFORCE] rebuilt_empty rows_in=96 rows_kept=0
   reason=no_body_inline_citations`` — the orphan-row policy (correct)
   emptied the rebuilt block, but the function still emitted the
   ``## 参考文献`` heading with zero rows. The user sees an empty
   references section.

Fixes:
- Heading patterns accept an optional ``N. `` / ``N、`` / ``(N)`` list
  prefix before the keyword.
- When the rebuilt block has zero rows, drop the heading too — an
  empty references section is worse than none (the report body has no
  citations to anchor).
"""

import pytest

from local_deep_research.text_optimization.citation_formatter import (
    enforce_sources_ascending_and_drop_orphans,
    strip_per_section_sources_block,
)


class TestNumberedRefHeading:
    def test_numbered_cjk_heading_stripped_per_section(self):
        body = (
            "正文段落引用 [1]。\n\n"
            "## 6. 参考资料\n\n"
            " FPV Racing Drone Research (2026-08-25) - 综合研究涵盖：\n"
            "- FPV竞速无人机的定义\n"
        )
        out = strip_per_section_sources_block(body)
        assert "6. 参考资料" not in out
        assert "FPV Racing Drone Research" not in out
        assert "正文段落引用" in out

    def test_numbered_english_heading_recognized(self):
        body = "Body text.\n\n## 3. References\n\n[1] Foo\n"
        out = strip_per_section_sources_block(body)
        assert "3. References" not in out

    def test_plain_heading_still_works(self):
        body = "Body text.\n\n## 参考文献\n\n[1] 甲\n"
        out = strip_per_section_sources_block(body)
        assert "参考文献" not in out


class TestEmptyRebuiltBlock:
    def _md(self) -> str:
        # Body has NO inline citations; Sources has 2 rows → all rows
        # are orphan-dropped → rebuilt block is empty.
        return (
            "# 报告\n\n"
            "正文没有任何内联引用标记。\n\n"
            "## 参考文献\n\n"
            "[1] 甲 (source nr: 1)\n"
            "   URL: http://a.onion/x\n\n"
            "[2] 乙 (source nr: 2)\n"
            "   URL: http://b.onion/y\n"
        )

    def test_empty_block_heading_dropped(self):
        out = enforce_sources_ascending_and_drop_orphans(self._md())
        assert "## 参考文献" not in out, (
            "empty rebuilt block must not keep the heading"
        )
        assert "正文" in out

    def test_nonempty_block_keeps_heading(self):
        md = (
            "# 报告\n\n"
            "正文引用 [1]。\n\n"
            "## 参考文献\n\n"
            "[1] 甲 (source nr: 1)\n"
            "   URL: http://a.onion/x\n"
        )
        out = enforce_sources_ascending_and_drop_orphans(md)
        assert "## 参考文献" in out
        assert "[1]" in out
