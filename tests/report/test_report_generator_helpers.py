"""Tests for ReportGenerator helper methods.

Covers _truncate_at_sentence_boundary and _build_previous_context — two
pure-logic helpers with zero prior test coverage that are critical for
report quality and repetition avoidance.
"""

from unittest.mock import MagicMock

import pytest

from local_deep_research.report_generator import IntegratedReportGenerator


@pytest.fixture
def generator():
    """Create an IntegratedReportGenerator with mocked dependencies."""
    mock_llm = MagicMock()
    mock_search = MagicMock()
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    gen.llm = mock_llm
    gen.search_system = mock_search
    gen.max_context_sections = 3
    gen.max_context_chars = 4000
    # `_format_final_report` reads `searches_per_section` for metadata
    # and `_section_documents_per_subsection` to decide whether to run
    # the citation-alignment pass.
    gen.searches_per_section = 2
    gen._section_documents_per_subsection = []
    return gen


# ── _truncate_at_sentence_boundary ──


class TestTruncateAtSentenceBoundary:
    """Tests for _truncate_at_sentence_boundary."""

    def test_text_shorter_than_limit_returned_unchanged(self, generator):
        text = "Short text."
        assert generator._truncate_at_sentence_boundary(text, 100) == text

    def test_text_exactly_at_limit_returned_unchanged(self, generator):
        text = "Exact." + "x" * 94  # 100 chars
        assert generator._truncate_at_sentence_boundary(text, 100) == text

    def test_truncates_at_period_followed_by_space(self, generator):
        text = "First sentence. Second sentence. Third sentence that goes on."
        result = generator._truncate_at_sentence_boundary(text, 35)
        assert result.startswith("First sentence. Second sentence.")
        assert result.endswith("\n[...truncated]")

    def test_truncates_at_exclamation_mark(self, generator):
        text = "Wow! This is amazing! More content follows here."
        result = generator._truncate_at_sentence_boundary(text, 25)
        assert "Wow! This is amazing!" in result
        assert "[...truncated]" in result

    def test_truncates_at_question_mark(self, generator):
        text = "Is this working? Yes it is working perfectly fine here."
        result = generator._truncate_at_sentence_boundary(text, 20)
        assert "Is this working?" in result
        assert "[...truncated]" in result

    def test_boundary_at_end_of_truncated_text(self, generator):
        # Period at exactly the last position of truncated text
        text = "Hello." + "x" * 100
        result = generator._truncate_at_sentence_boundary(text, 6)
        # "Hello." is 6 chars, truncated[:6] = "Hello."
        # last_sentence_end = 6, min_acceptable = int(6*0.8)=4, 6 > 4 → use boundary
        assert "Hello." in result
        assert "[...truncated]" in result

    def test_no_sentence_boundary_falls_back_to_hard_truncation(
        self, generator
    ):
        text = "a" * 200
        result = generator._truncate_at_sentence_boundary(text, 100)
        assert result == "a" * 100 + "\n[...truncated]"

    def test_sentence_boundary_too_early_falls_back(self, generator):
        # Period at position 5 out of 100 → below 80% threshold
        text = "Hi. " + "x" * 200
        result = generator._truncate_at_sentence_boundary(text, 100)
        # min_acceptable = 80, last_sentence_end = 4, 4 < 80 → hard truncation
        assert result == text[:100] + "\n[...truncated]"

    def test_sentence_boundary_at_80_percent_threshold(self, generator):
        # Exactly at 80% boundary
        # max_chars=100, min_acceptable=80
        text = "x" * 80 + ". " + "y" * 50
        result = generator._truncate_at_sentence_boundary(text, 100)
        # last_sentence_end=81, min_acceptable=80, 81 > 80 → use boundary
        assert result.endswith("\n[...truncated]")
        assert result.startswith("x" * 80 + ".")

    def test_period_followed_by_newline(self, generator):
        text = "First sentence.\nSecond sentence continues for a while here."
        result = generator._truncate_at_sentence_boundary(text, 20)
        assert "First sentence." in result
        assert "[...truncated]" in result

    def test_period_not_followed_by_space_or_newline_ignored(self, generator):
        # "3.14" has a period but it's followed by a digit, not space
        text = "The value is 3.14 and more text follows after that point."
        result = generator._truncate_at_sentence_boundary(text, 20)
        # Only sentence boundaries followed by space/newline are valid
        # In "The value is 3.14 a", the period at index 14 is followed by '1', not space
        # So falls back to hard truncation
        assert result == text[:20] + "\n[...truncated]"

    def test_empty_text(self, generator):
        assert generator._truncate_at_sentence_boundary("", 100) == ""

    def test_single_character(self, generator):
        assert generator._truncate_at_sentence_boundary("a", 100) == "a"

    def test_multiple_sentence_endings_uses_last_valid(self, generator):
        text = "One. Two. Three. Four. Five. Six. Seven. Eight."
        result = generator._truncate_at_sentence_boundary(text, 30)
        # Should find the last boundary within the first 30 chars
        # "One. Two. Three. Four. Five. " is 29 chars
        assert "[...truncated]" in result


# ── _build_previous_context ──


class TestBuildPreviousContext:
    """Tests for _build_previous_context."""

    def test_empty_list_returns_empty_string(self, generator):
        assert generator._build_previous_context([]) == ""

    def test_single_finding_included(self, generator):
        result = generator._build_previous_context(["Finding 1"])
        assert "Finding 1" in result
        assert "DO NOT REPEAT" in result
        assert "CONTENT ALREADY WRITTEN" in result

    def test_respects_max_context_sections_limit(self, generator):
        generator.max_context_sections = 2
        findings = ["Finding 1", "Finding 2", "Finding 3", "Finding 4"]
        result = generator._build_previous_context(findings)
        # Should only include last 2 findings
        assert "Finding 3" in result
        assert "Finding 4" in result
        assert "Finding 1" not in result
        assert "Finding 2" not in result

    def test_joins_with_separator(self, generator):
        result = generator._build_previous_context(["A", "B"])
        assert "\n\n---\n\n" in result

    def test_truncates_long_context(self, generator):
        generator.max_context_chars = 50
        long_finding = "x" * 100
        result = generator._build_previous_context([long_finding])
        # The content should be truncated
        assert "[...truncated]" in result

    def test_formatting_markers_present(self, generator):
        result = generator._build_previous_context(["Test content"])
        assert "=== CONTENT ALREADY WRITTEN (DO NOT REPEAT) ===" in result
        assert "=== END OF PREVIOUS CONTENT ===" in result
        assert "CRITICAL:" in result

    def test_context_within_char_limit_not_truncated(self, generator):
        generator.max_context_chars = 10000
        result = generator._build_previous_context(["Short finding."])
        assert "[...truncated]" not in result

    def test_uses_last_n_sections(self, generator):
        generator.max_context_sections = 3
        findings = [f"Finding {i}" for i in range(10)]
        result = generator._build_previous_context(findings)
        assert "Finding 7" in result
        assert "Finding 8" in result
        assert "Finding 9" in result
        assert "Finding 0" not in result


# ── _format_final_report citation renumbering + alignment ──


class TestFormatFinalReportCitationRenumbering:
    """Tests for the citation-alignment pass added to
    `_format_final_report`. The pass is enabled when
    `_section_documents_per_subsection` is set on the generator; the
    absence of that attribute preserves the legacy
    `format_links_to_markdown(all_links_of_system)` path."""

    @staticmethod
    def _make_doc(idx, title, source):
        from langchain_core.documents import Document

        return Document(
            page_content="content",
            metadata={"index": idx, "title": title, "source": source},
        )

    def test_body_citation_matches_sources_block_url(self, generator):
        """The user-reported bug: body [N] must point at the same URL as
        the Sources-block row. With per-subsection documents supplied,
        the alignment is guaranteed by construction."""
        all_docs = [
            self._make_doc(3, "Doc 3", "http://real-url-3"),
            self._make_doc(7, "Doc 7", "http://real-url-7"),
        ]
        sections = {
            "Section One": "Paragraph about real-url-3 [3]. "
            "Paragraph about real-url-7 [7]."
        }
        structure = [{"name": "Section One", "subsections": []}]
        generator._section_documents_per_subsection = [all_docs]
        # Legacy list — must be ignored when per-subsection docs are
        # present.
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, structure, query="q"
        )
        body, sources = result["content"].split("## Sources")

        # Body uses sequential 1..N numbering.
        assert "[1]" in body
        assert "[2]" in body
        # Old non-contiguous numbers are gone.
        assert "[3]" not in body
        assert "[7]" not in body
        # Both real URLs are present in the Sources block.
        assert "http://real-url-3" in sources
        assert "http://real-url-7" in sources
        # First-cite order: [3] appeared first in body → [1] is the
        # Jinmao doc, [2] is the second one.
        assert body.index("[1]") < body.index("[2]")

    def test_hallucinated_marker_removed_from_body(self, generator):
        all_docs = [self._make_doc(1, "Real", "http://real")]
        sections = {"S": "Real content [1]. Ghost [768]."}
        generator._section_documents_per_subsection = [all_docs]
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, [{"name": "S", "subsections": []}], query="q"
        )
        body = result["content"].split("## Sources")[0]
        assert "[1]" in body
        assert "[768]" not in body

    def test_sequential_1_to_n_numbering_out_of_order_cites(
        self, generator
    ):
        all_docs = [
            self._make_doc(5, "D5", "http://x5"),
            self._make_doc(2, "D2", "http://x2"),
            self._make_doc(9, "D9", "http://x9"),
        ]
        # Body cites out of original order: [9] first, then [5], [2].
        sections = {"S": "A [9] B [5] C [2]"}
        generator._section_documents_per_subsection = [all_docs]
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, [{"name": "S", "subsections": []}], query="q"
        )
        body = result["content"].split("## Sources")[0]
        # Sequential 1..N.
        assert "[1]" in body
        assert "[2]" in body
        assert "[3]" in body
        # First-cite order: [9] was first → becomes [1], whose URL is x9.
        assert "http://x9" in result["content"]
        # Sources block lists each unique URL once.
        assert "http://x5" in result["content"]
        assert "http://x2" in result["content"]

    def test_no_documents_keeps_legacy_path(self, generator):
        """When `_section_documents_per_subsection` is absent/empty,
        fall through to the existing `format_links_to_markdown`
        behaviour that reads `all_links_of_system`."""
        sections = {"S": "Body [1]"}
        generator.search_system.all_links_of_system = [
            {
                "url": "http://x",
                "link": "http://x",
                "title": "T",
                "index": "1",
                "journal_quality": None,
                "metadata": {},
            }
        ]
        result = generator._format_final_report(
            sections, [{"name": "S", "subsections": []}], query="q"
        )
        assert "## Sources" in result["content"]
        assert "http://x" in result["content"]

