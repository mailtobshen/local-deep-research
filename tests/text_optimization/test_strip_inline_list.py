"""Tests for R2 (drop (source nr: N) suffix) and R3
(strip inline reference list without heading).

R2: the primary path's ## Sources block no longer carries the
``(source nr: N)`` debug suffix. The new_idx is enough — body
``[[N]](url)`` ↔ sources ``[N]`` is the only invariant needed.

R3: LLM (qwen3.5-opus:9b on Aug 5) emitted a tail block of bare
``[N] Title — URL: ...`` rows without any heading. The
heading-aware strip_per_section_sources_block misses these. The new
strip_inline_reference_list detects a tail cluster of 3+ matching
rows and truncates before the cluster.
"""

import re
import sys
from unittest.mock import MagicMock

sys.path.insert(0, "src")

from local_deep_research.text_optimization.citation_formatter import (
    strip_inline_reference_list,
)


# ---- R2: no (source nr: N) suffix in ## Sources block ----

class TestNoSourceNrSuffixInSources:
    """End-to-end: the primary path's ## Sources block must not
    carry the (source nr: N) suffix."""

    def _run_primary_path(self, body, all_links):
        from local_deep_research.report_generator import (
            IntegratedReportGenerator,
        )

        rg = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
        # Force primary path (per_subsection_docs non-empty)
        from langchain_core.documents import Document
        rg._section_documents_per_subsection = [
            [
                Document(
                    page_content="",
                    metadata={
                        "index": i + 1,
                        "source": link["link"],
                        "title": link["title"],
                    },
                )
                for i, link in enumerate(all_links)
            ]
        ]
        rg.search_system = MagicMock()
        rg.search_system.all_links_of_system = all_links
        rg.searches_per_section = {}

        sections = {"S1": body}
        structure = [
            {"name": "S1", "subsections": [{"name": "s", "purpose": "p"}]},
        ]
        result = rg._format_final_report(sections, structure, "test")
        return result["content"]

    def test_sources_block_no_source_nr_suffix(self):
        body = "Foo [[1]](https://a) bar"
        all_links = [{"link": "https://a", "title": "T1"}]
        content = self._run_primary_path(body, all_links)
        # The (source nr: N) suffix must not appear
        assert "(source nr:" not in content, (
            f"R2 violated: (source nr:) still in final report:\n{content}"
        )

    def test_sources_block_still_has_url(self):
        """URL preservation invariant: dropping (source nr:) must not
        accidentally drop URL too."""
        body = "Foo [[1]](https://a)"
        all_links = [{"link": "https://a", "title": "T1"}]
        content = self._run_primary_path(body, all_links)
        assert "URL: https://a" in content


# ---- R3: strip inline reference list without heading ----

class TestStripInlineRefList:
    """Tail cluster of 3+ `[N] Title` or `[N] Title — URL:` rows
    is treated as an LLM-written reference list."""

    def test_strips_three_url_rows_at_tail(self):
        body = (
            "Foo bar\n\n"
            "[1] Title one — URL: https://a\n"
            "[2] Title two — URL: https://b\n"
            "[3] Title three — URL: https://c\n"
        )
        out = strip_inline_reference_list(body)
        assert "[1] Title one" not in out
        assert "Foo bar" in out

    def test_preserves_two_rows_at_tail(self):
        """Only 2 rows is below threshold (3) — keep them."""
        body = (
            "Foo bar\n\n"
            "[1] Title one\n"
            "[2] Title two\n"
        )
        out = strip_inline_reference_list(body)
        assert out == body

    def test_preserves_inline_citation_in_prose(self):
        """A single [1] mention in prose is NOT a list — must keep."""
        body = "Foo [1] bar baz qux"
        out = strip_inline_reference_list(body)
        assert out == body

    def test_strips_plain_title_rows(self):
        """[N] Title (no URL line) is also a ref-list row."""
        body = (
            "Foo\n\n"
            "[1] Some title\n"
            "[2] Another title\n"
            "[3] Third title\n"
        )
        out = strip_inline_reference_list(body)
        assert "[1] Some title" not in out
        assert "Foo" in out

    def test_mixed_url_and_plain_rows(self):
        body = (
            "Foo\n\n"
            "[1] First — URL: https://a\n"
            "[2] Second\n"
            "[3] Third — URL: https://c\n"
        )
        out = strip_inline_reference_list(body)
        assert "[1] First" not in out
        assert "[3] Third" not in out
        assert "Foo" in out

    def test_preserves_short_body(self):
        body = "Just one line"
        out = strip_inline_reference_list(body)
        assert out == body

    def test_preserves_body_with_embedded_citations(self):
        """Body with [[N]](url) inline citations should NOT trigger
        the strip — only the `[N] Title` plain form matches."""
        body = (
            "Foo [[1]](https://a) and [[2]](https://b)\n"
            "More text [[3]](https://c) and [[4]](https://d)\n"
        )
        out = strip_inline_reference_list(body)
        # No plain [N] Title pattern → no strip
        assert out == body

    def test_strips_with_blank_line_separator(self):
        """A blank line between body and tail list is allowed."""
        body = (
            "Foo bar\n\n"
            "\n"
            "[1] A\n"
            "[2] B\n"
            "[3] C\n"
        )
        out = strip_inline_reference_list(body)
        # Should still strip (blank line is OK between body and list)
        assert "[1] A" not in out

    def test_does_not_strip_mid_body_list(self):
        """A 3-row list embedded mid-document (not at tail) is NOT
        stripped — only tail-attached lists are removed."""
        body = (
            "Foo\n\n"
            "[1] Mid A\n"
            "[2] Mid B\n"
            "[3] Mid C\n\n"
            "End paragraph after list\n"
        )
        out = strip_inline_reference_list(body)
        # List is mid-document with content after it → keep
        assert out == body

    def test_strips_then_dedup_doesnt_lose_body(self):
        """End-to-end: after strip, the surviving body should still
        contain its inline citations."""
        body = (
            "Foo [[1]](https://a) bar [[2]](https://b)\n\n"
            "[1] Title A — URL: https://a\n"
            "[2] Title B — URL: https://b\n"
            "[3] Title C — URL: https://c\n"
        )
        out = strip_inline_reference_list(body)
        # Inline [[N]](url) preserved
        assert "[[1]](https://a)" in out
        assert "[[2]](https://b)" in out
        # Tail list stripped
        assert "Title A" not in out
        assert "Title C" not in out