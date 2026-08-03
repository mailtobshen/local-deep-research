"""Edge-case tests for citation_formatter — idempotency, parsing, and export boundaries."""


def _make_document(body, sources):
    """Helper: combine body text with a sources section."""
    return body + "\n\n## Sources\n" + sources


class TestAlreadyFormattedCitations:
    """Ensure already-formatted citations are not double-processed."""

    def test_already_formatted_not_rematched(self):
        """[[1]](url) should NOT be re-formatted to [[[1]]](url)."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        doc = _make_document(
            "See [[1]](https://example.com) for details.",
            "[1] Example Source\n  URL: https://example.com",
        )
        result = formatter.format_document(doc)
        # The negative lookbehind (?<![\[) should prevent matching inside [[ ]]
        assert "[[[1]]]" not in result

    def test_format_document_idempotent(self):
        """Calling format_document twice produces identical output."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        doc = _make_document(
            "See [1] for details.",
            "[1] Example Source\n  URL: https://example.com",
        )
        first = formatter.format_document(doc)
        second = formatter.format_document(first)
        assert first == second


class TestSourceParsing:
    """Tests for _parse_sources edge cases."""

    def test_parse_sources_tab_indented_url(self):
        """Tab indentation in \\tURL: should be parsed."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        sources_text = "## Sources\n[1] Title here\n\tURL: https://example.com"
        sources = formatter._parse_sources(sources_text)
        # The regex uses \\s* which matches tabs
        assert "1" in sources
        _, url = sources["1"]
        assert url == "https://example.com"


class TestDomainIdAssignment:
    """Tests for domain ID assignment order."""

    def test_domain_id_assignment_preserves_source_order(self):
        """Source 1 gets -1, source 5 gets -2 deterministically for same domain."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.DOMAIN_ID_ALWAYS_HYPERLINKS)
        doc = _make_document(
            "See [1] and [5].",
            "[1] First Article\n  URL: https://arxiv.org/abs/1234\n"
            "[5] Second Article\n  URL: https://arxiv.org/abs/5678",
        )
        result = formatter.format_document(doc)
        # Both are from arxiv.org, should get -1 and -2
        assert "arxiv.org-1" in result
        assert "arxiv.org-2" in result


class TestRISExporterBoundary:
    """Tests for RIS exporter deduplication boundary."""

    def test_ris_exporter_stops_at_all_sources(self):
        """Deduplication boundary at '## ALL SOURCES' section."""
        from local_deep_research.text_optimization.citation_formatter import (
            RISExporter,
        )

        exporter = RISExporter()
        content = (
            "## Sources\n"
            "[1] First Source\n  URL: https://example.com\n\n"
            "## ALL SOURCES\n"
            "[1] First Source (duplicate)\n  URL: https://example.com\n"
            "[2] Second Source\n  URL: https://other.com"
        )
        ris = exporter.export_to_ris(content)
        # Should only include source [1] from before the ALL SOURCES marker
        assert "ref1" in ris
        assert "ref2" not in ris


class TestLaTeXListBalance:
    """Tests for LaTeX list conversion."""

    def test_latex_list_balanced_with_empty_lines(self):
        """\\begin{itemize} and \\end{itemize} are always balanced."""
        from local_deep_research.text_optimization.citation_formatter import (
            LaTeXExporter,
        )

        exporter = LaTeXExporter()
        content = (
            "# Title\n\n- Item one\n- Item two\n\nSome text\n\n- Item three\n"
        )
        result = exporter.export_to_latex(content)
        begin_count = result.count("\\begin{itemize}")
        end_count = result.count("\\end{itemize}")
        assert begin_count == end_count
        assert begin_count >= 1


class TestSourceWordPattern:
    """Tests for 'Source X' pattern matching."""

    def test_source_word_with_trailing_period(self):
        """'Source 1.' at end-of-sentence converts correctly."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        doc = _make_document(
            "According to Source 1.",
            "[1] Example\n  URL: https://example.com",
        )
        result = formatter.format_document(doc)
        # "Source 1" should be replaced; the period remains
        assert "Source 1" not in result.split("## Sources")[0]

    def test_comma_citations_with_spaces(self):
        """[1, 2, 3] with varying space patterns."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
        )

        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        doc = _make_document(
            "See [1, 2, 3] for details.",
            "[1] Source A\n  URL: https://a.com\n"
            "[2] Source B\n  URL: https://b.com\n"
            "[3] Source C\n  URL: https://c.com",
        )
        result = formatter.format_document(doc)
        body = result.split("## Sources")[0]
        # Each citation should be individually formatted
        assert "[[1]]" in body
        assert "[[2]]" in body
        assert "[[3]]" in body


class TestCitationRenumbering:
    """Tests for the citation renumbering + hallucination-stripping
    helpers added to `text_optimization/citation_formatter.py`. The
    helpers back the citation-alignment fix in `report_generator.py`."""

    def test_build_first_cite_order_first_occurrence_wins(self):
        from local_deep_research.text_optimization.citation_formatter import (
            build_first_cite_order,
        )

        order = build_first_cite_order("A [3] B [1] C [3] D [2]", {1, 2, 3})
        assert order == [3, 1, 2]

    def test_build_first_cite_order_handles_comma_groups(self):
        from local_deep_research.text_optimization.citation_formatter import (
            build_first_cite_order,
        )

        body = "Quote [5, 2, 9] then [5] then [2]."
        order = build_first_cite_order(body, {2, 5, 9})
        assert order == [5, 2, 9]

    def test_build_first_cite_order_handles_hyperlink_form(self):
        from local_deep_research.text_optimization.citation_formatter import (
            build_first_cite_order,
        )

        # `[[7]](url)` is the same citation as `[7]`, just hyperlinked.
        body = "See [[7]](http://x) and [3]."
        order = build_first_cite_order(body, {3, 7})
        assert order == [7, 3]

    def test_strip_hallucinated_citations_removes_invalid_only(self):
        from local_deep_research.text_optimization.citation_formatter import (
            strip_hallucinated_citations,
        )

        body = "Real [1]. Hallucinated [768]. Comma [1, 999, 2]."
        out = strip_hallucinated_citations(body, {1, 2})
        assert "[1]" in out
        assert "[1, 2]" in out
        assert "[768]" not in out
        assert "999" not in out

    def test_strip_hallucinated_citations_strips_hyperlink_form(self):
        from local_deep_research.text_optimization.citation_formatter import (
            strip_hallucinated_citations,
        )

        body = "Real [[1]](http://x). Ghost [[768]](http://ghost)."
        out = strip_hallucinated_citations(body, {1})
        assert "[[1]](http://x)" in out
        assert "[[768]]" not in out
        assert "http://ghost" not in out

    def test_strip_hallucinated_citations_strips_source_word(self):
        from local_deep_research.text_optimization.citation_formatter import (
            strip_hallucinated_citations,
        )

        body = "See Source 1 and Source 768 for details."
        out = strip_hallucinated_citations(body, {1})
        assert "Source 1" in out
        assert "Source 768" not in out

    def test_renumber_citations_preserves_hyperlink_url(self):
        from local_deep_research.text_optimization.citation_formatter import (
            renumber_citations,
        )

        # Hyperlinked citations keep their original URL — only the index
        # is rewritten.
        body = "See [[3]](http://x) and [1]."
        out = renumber_citations(
            body,
            {1: ("T1", "http://y"), 2: ("T2", "http://z")},
            {3: 1, 1: 2},
        )
        assert "[[1]](http://x)" in out
        # Plain [1] gets the new index 2; sources[2] supplies the URL.
        assert "[[2]](http://z)" in out

    def test_renumber_citations_falls_back_to_plain_when_no_url(self):
        from local_deep_research.text_optimization.citation_formatter import (
            renumber_citations,
        )

        out = renumber_citations("See [3].", {1: ("T", "")}, {3: 1})
        assert "[1]" in out
        assert "[" + "[1]" + "]" not in out  # no double-bracket form

    def test_renumber_then_format_document_idempotent(self):
        """Renumbering must not break the existing `format_document`
        idempotency invariant (compare against
        `test_format_document_idempotent` elsewhere in this file)."""
        from local_deep_research.text_optimization.citation_formatter import (
            CitationFormatter,
            CitationMode,
            renumber_citations,
        )

        body = "See [3] and [1].\n\n## Sources\n[1] T1\n   URL: http://y\n[3] T3\n   URL: http://x\n"
        renumbered = renumber_citations(
            body,
            {1: ("T1", "http://y"), 2: ("T3", "http://x")},
            {3: 1, 1: 2},
        )
        formatter = CitationFormatter(CitationMode.NUMBER_HYPERLINKS)
        first = formatter.format_document(renumbered)
        second = formatter.format_document(first)
        assert first == second

