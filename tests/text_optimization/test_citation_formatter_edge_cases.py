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
        assert "[\\[1\\]](" in body
        assert "[\\[2\\]](" in body
        assert "[\\[3\\]](" in body


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
        assert "[[1]](http://x)" in out  # strip keeps token as-is (no renumber)
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
        assert "[\\[1\\]](http://x)" in out
        # Plain [1] gets the new index 2; sources[2] supplies the URL.
        assert "[\\[2\\]](http://z)" in out

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


class TestEnforceSourcesAscending:
    """Tests for ``enforce_sources_ascending_and_drop_orphans``.

    The enforcer must:

    1. Rewrite the trailing ``## Sources`` block so the displayed
       bracket numbers are strictly ascending ``[1]..[N]``.
    2. Delete in-body citation markers whose URL has no matching
       Sources entry (no orphan-preservation; no missing-sources
       appendix).
    """

    def _enforce(self, content: str) -> str:
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        return enforce_sources_ascending_and_drop_orphans(content)

    def _displayed_n(self, content: str):
        import re

        # Find the heading position regardless of language.
        from local_deep_research.text_optimization.citation_formatter import (
            find_sources_section,
        )

        start = find_sources_section(content)
        if start < 0:
            return []
        tail = content[start:]
        return [int(x) for x in re.findall(r"^\[(\d+)\]", tail, re.MULTILINE)]

    def test_ascending_enforced_when_display_already_ascending(self):
        """A correctly-ordered block passes through unchanged in
        displayed numbering; only URLs are normalised (slash and
        trailing paren harmonisation)."""
        content = (
            "# R\n\n"
            "See [[3]](https://foo.example) and [[1]](https://bar.example).\n\n"
            "## Sources\n\n"
            "[1] Bar\n   URL: https://bar.example\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # Displayed numbers are ascending in body-first-cite order.
        # Body first cites [3] (foo), then [1] (bar) — so new [1]=foo,
        # new [2]=bar.
        assert self._displayed_n(out) == [1, 2]
        # Body markers were renumbered to point at the new indices.
        assert "[\\[1\\]](https://foo.example)" in out
        assert "[\\[2\\]](https://bar.example)" in out

    def test_ascending_enforced_when_display_non_ascending(self):
        """The original bug — displayed [7], [4], [5], [3], ... — must
        be reshuffled into strictly ascending [1]..[N]."""
        content = (
            "# R\n\n"
            "Body cites [3], [7], and [1] in that order.\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "[7] Bar\n   URL: https://bar.example\n\n"
            "[1] Baz\n   URL: https://baz.example\n\n"
        )
        out = self._enforce(content)
        assert self._displayed_n(out) == [1, 2, 3]

    def test_orphan_body_marker_deleted(self):
        """A body marker whose URL is missing from the Sources block
        is deleted; the body keeps the rest of the sentence intact."""
        content = (
            "# R\n\n"
            "Real cite [[3]](https://foo.example). "
            "Orphan cite [[9]](https://missing.example).\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # The only Sources entry renumbers to [1]; orphan [9] is dropped.
        assert "[\\[1\\]](https://foo.example)" in out
        assert "missing.example" not in out
        assert "[[9]]" not in out
        assert "[9]" not in out

    def test_orphan_plain_marker_deleted(self):
        """Plain (non-hyperlinked) ``[N]`` whose number has no
        matching Sources entry is deleted too."""
        content = (
            "# R\n\n"
            "Real [3]. Orphan [9].\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # The only Sources entry renumbers to [1] and is hyperlinked;
        # the orphan [9] is dropped.
        assert "[\\[1\\]](https://foo.example)" in out
        assert "[9]" not in out
        assert "missing" not in out.lower() or "Orphan" in out

    def test_no_sources_block_is_noop(self):
        """When there is no trailing ``## Sources``, the content is
        returned unchanged."""
        content = "# R\n\nJust a body with [1] and no sources block."
        out = self._enforce(content)
        assert out == content

    def test_url_normalization_matches_body_to_sources(self):
        """Body URL with a trailing slash matches the Sources URL
        without it. Otherwise the enforcer would falsely flag the
        body marker as an orphan."""
        content = (
            "# R\n\n"
            "Cite [[3]](https://foo.example/page/).\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example/page\n\n"
        )
        out = self._enforce(content)
        assert "foo.example/page" in out

    def test_url_normalization_balances_trailing_paren(self):
        """Body URL missing its closing ``)`` still matches the
        Sources URL that has one. The canonicalizer adds the missing
        closing paren."""
        content = (
            "# R\n\n"
            "Cite [[3]](https://ooh.example/venue/201/tianzifang-(tian-zi-fang).\n\n"
            "## Sources\n\n"
            "[3] OOH\n   URL: https://ooh.example/venue/201/tianzifang-(tian-zi-fang\n\n"
        )
        out = self._enforce(content)
        assert "ooh.example" in out

    def test_url_normalization_drops_stray_trailing_close_paren(self):
        """Body URL with an unbalanced trailing ``)`` matches a
        Sources URL without one."""
        content = (
            "# R\n\n"
            "Cite [[3]](https://ooh.example/venue/201/tianzifang-(tian-zi-fang)).\n\n"
            "## Sources\n\n"
            "[3] OOH\n   URL: https://ooh.example/venue/201/tianzifang-(tian-zi-fang\n\n"
        )
        out = self._enforce(content)
        assert "ooh.example" in out

    def test_comma_group_in_body_is_expanded(self):
        """Comma-group body markers are expanded into individual
        ``[N]`` tokens before renumbering so each member is
        rewritten independently."""
        content = (
            "# R\n\n"
            "Cite [3, 7].\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "[7] Bar\n   URL: https://bar.example\n\n"
        )
        out = self._enforce(content)
        # Comma group must be expanded into per-cite markers.
        assert "[3, 7]" not in out.split("## Sources")[0]
        # Each cite resolves to its new index.
        assert "[\\[1\\]](https://foo.example)" in out
        assert "[\\[2\\]](https://bar.example)" in out

    def test_idempotent(self):
        """Re-running the enforcer on already-enforced content is a
        no-op."""
        first = self._enforce(
            "# R\n\nCite [3] and [7].\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "[7] Bar\n   URL: https://bar.example\n\n"
        )
        second = self._enforce(first)
        assert first == second

    def test_cjk_sources_heading(self):
        """CJK ``## 参考文献`` variant is recognised and enforced."""
        content = (
            "# R\n\n"
            "Cite [3].\n\n"
            "## 参考文献\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        assert self._displayed_n(out) == [1]
        assert "## 参考文献" in out

    def test_bold_inline_cjk_heading_is_also_covered(self):
        """LLM-invented bold inline ``**参考资料：**`` block (research
        f73ce631, 2026-08-26) followed by a proper ``## 参考文献``
        heading — the bold block must NOT survive. Before the
        bold-inline patterns were added, ``find_sources_section``
        anchored on ``^#{1,3}`` and the bold inline heading was
        invisible to it; the rebuild then emitted a SECOND
        ``## 参考文献`` block below the original, leaving the user
        with two references sections (one unsorted, one ascending).
        """
        content = (
            "# R\n\n"
            "外滩 [3]. 迪士尼 [1].\n\n"
            "**参考资料：**\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "[1] Bar\n   URL: https://bar.example\n\n"
            "## 参考文献\n\n"
            "[1] Bar\n   URL: https://bar.example\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # The bold inline heading must not survive.
        assert "**参考资料：**" not in out, (
            "Bold inline heading survived the enforcer; the user would "
            "see two references sections (the unsorted LLM block + the "
            "ascending rebuilt block)."
        )
        # The rebuilt block must be ascending and complete (both sources
        # cited, renumbered 1..2 in body-first-cite order).
        assert self._displayed_n(out) == [1, 2], (
            "Expected both sources to survive the rebuild in body-first "
            f"cite order, got: {self._displayed_n(out)}"
        )
        # The canonical heading text must be present exactly once
        # (no duplication).
        assert out.count("## 参考文献") == 1
        # The unsorted list under the LLM's bold heading must be gone —
        # only the rebuilt ascending list remains.
        ascending_indices = [
            line
            for line in out.splitlines()
            if line.startswith("[")
            and line.split("]")[0][1:].isdigit()
        ]
        # Find where the canonical sources block starts (after ## 参考文献)
        canonical_start = out.index("## 参考文献")
        before_canonical = out[:canonical_start]
        assert "[3] Foo" not in before_canonical, (
            "The LLM's unsorted [3] Foo (under the bold heading) should "
            "have been sliced off; it survived in the body."
        )

    def test_bold_inline_english_heading_is_also_covered(self):
        """LLM-invented bold inline ``**Sources:**`` block — same
        coverage as the CJK variant above."""
        content = (
            "# R\n\n"
            "Cite [3].\n\n"
            "**Sources:**\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        assert "**Sources:**" not in out
        assert self._displayed_n(out) == [1]
        assert "## Sources" in out

    def test_dedupes_by_canonical_url(self):
        """Two body markers to the same canonical URL produce ONE
        Sources row, not two."""
        content = (
            "# R\n\n"
            "First [[3]](https://foo.example) and second [[3]](https://foo.example).\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # Only one entry survives.
        assert self._displayed_n(out) == [1]
        # Both body markers remain (they resolve to the same new [1]).
        assert out.count("foo.example") >= 3  # 2 body + 1 source

    def test_strips_existing_source_nr_suffix_before_re_emitting(self):
        """If a parsed Sources row already carries ``(source nr: N)``,
        the rebuilt entry does NOT duplicate it. Otherwise we'd
        emit ``(source nr: 60) (source nr: 47)``."""
        content = (
            "# R\n\n"
            "Cite [3].\n\n"
            "## Sources\n\n"
            "[3] Foo (source nr: 60)\n   URL: https://foo.example\n\n"
        )
        out = self._enforce(content)
        # Exactly one "(source nr:" per entry.
        assert out.count("(source nr:") == 1
        assert "Foo" in out
        assert "foo.example" in out

    def test_no_zhang_guan_li_dai_with_byte_different_urls(self):
        """REGRESSION GUARD — 张冠李戴 prevention.

        Two Sources rows whose URLs differ at the byte level (e.g.
        trailing slash, tracking param, different path) are NOT
        duplicates — they are kept as distinct entries. Body
        markers resolve to the row whose URL they actually point
        at, never to a different row.
        """
        content = (
            "# R\n\n"
            "First paper [3](https://arxiv.org/abs/111).\n"
            "Second paper [7](https://arxiv.org/abs/111/?v=2).\n\n"
            "## Sources\n\n"
            "[3] March paper - Authors A, B (source nr: 3)\n"
            "   URL: https://arxiv.org/abs/111\n\n"
            "[7] July paper - Authors C, D (source nr: 7)\n"
            "   URL: https://arxiv.org/abs/111/?v=2\n\n"
        )
        out = self._enforce(content)
        # Both rows preserved as distinct entries.
        assert "March paper" in out
        assert "July paper" in out
        # Body markers are distinct and point at their own rows.
        first_block = out.split("## Sources")[0]
        assert first_block.count("[\\[1\\]](https://arxiv.org/abs/111)") == 1
        assert first_block.count("[\\[2\\]](https://arxiv.org/abs/111/?v=2)") == 1
        # Both Sources entries present.
        sources_tail = out.split("## Sources")[1]
        assert sources_tail.count("https://arxiv.org/abs/111") == 2

    def test_trailing_slash_dedups_as_canonical_equal(self):
        """Two Sources rows whose URLs differ only by a trailing
        slash canonicalise to the same string under
        canonical_url_key and are therefore treated as duplicates
        — the LLM wrote the same source twice with slightly
        different formatting. They collapse to one entry.
        """
        content = (
            "# R\n\n"
            "Real [5](https://example.com/page/).\n"
            "And another [9](https://example.com/page) about something else.\n\n"
            "## Sources\n\n"
            "[5] Foo (source nr: 5)\n   URL: https://example.com/page/\n\n"
            "[9] Bar (source nr: 9)\n   URL: https://example.com/page\n\n"
        )
        out = self._enforce(content)
        # Both rows merge to a single entry — canonical treats
        # /page and /page/ as the same URL.
        assert self._displayed_n(out) == [1]
        body = out.split("## Sources")[0]
        # Both body markers renumber to the winner.
        assert body.count("[\\[1\\]](https://example.com/page") == 2

    def test_different_path_or_query_does_not_dedup(self):
        """URLs that differ in PATH or in non-tracking query
        parameters are NOT collapsed by the canonical-then-exact
        dedup rule — those are genuinely different resources.
        """
        content = (
            "# R\n\n"
            "March [3](https://arxiv.org/abs/111). "
            "July [7](https://arxiv.org/abs/111/?v=2).\n\n"
            "## Sources\n\n"
            "[3] March\n"
            "   URL: https://arxiv.org/abs/111\n\n"
            "[7] July\n"
            "   URL: https://arxiv.org/abs/111/?v=2\n\n"
        )
        out = self._enforce(content)
        # Both rows preserved (canonical keeps the ?v=2 query).
        assert self._displayed_n(out) == [1, 2]
        body = out.split("## Sources")[0]
        assert body.count("[\\[1\\]](https://arxiv.org/abs/111)") == 1
        assert body.count("[\\[2\\]](https://arxiv.org/abs/111/?v=2)") == 1

    def test_uncited_rows_are_dropped(self):
        """2026-08-23 policy: the rebuilt block contains ONLY rows the
        body cites (and that carry a URL), numbered 1..N ascending.
        Uncited rows are dropped entirely."""
        content = (
            "# R\n\n"
            "Cite [3].\n\n"
            "## Sources\n\n"
            "[3] Foo\n   URL: https://foo.example\n\n"
            "[7] Uncited\n   URL: https://bar.example\n\n"
        )
        out = self._enforce(content)
        sources_tail = out.split("## Sources")[1]
        assert "[1] Foo" in sources_tail
        assert "Uncited" not in sources_tail
        assert "[2]" not in sources_tail
        body = out.split("## Sources")[0]
        assert "[[2]]" not in body

    def test_dedup_same_url_same_title_merges(self):
        """Two Sources rows that share BOTH canonical URL AND
        normalised title are the LLM-hallucination duplicate case:
        the same source written twice. They collapse into a single
        Sources entry, and every body marker pointing at either
        row's displayed_n renumbers to the surviving entry.
        """
        content = (
            "# R\n\n"
            "First [3](https://en.wikipedia.org/wiki/Shanghai) "
            "and again [7](https://en.wikipedia.org/wiki/Shanghai).\n\n"
            "## Sources\n\n"
            "[3] Shanghai - Wikipedia\n"
            "   URL: https://en.wikipedia.org/wiki/Shanghai\n\n"
            "[7] Shanghai - Wikipedia\n"
            "   URL: https://en.wikipedia.org/wiki/Shanghai\n\n"
        )
        out = self._enforce(content)
        sources_tail = out.split("## Sources")[1]
        # Only one Sources entry survives.
        displayed = self._displayed_n(out)
        assert displayed == [1]
        # Both body markers renumber to the surviving entry.
        body = out.split("## Sources")[0]
        assert body.count("[\\[1\\]](https://en.wikipedia.org/wiki/Shanghai)") == 2

    def test_dedup_same_url_different_titles_merges(self):
        """Two Sources rows that share the SAME byte-URL (even with
        distinct titles) collapse to a single entry — in research
        reports the dominant pattern is LLM-hallucinated
        duplicates of the same source, not two genuinely distinct
        papers sharing one URL. The body marker for the dropped
        row's N renumbers onto the winner.
        """
        content = (
            "# R\n\n"
            "March [3](https://arxiv.org/abs/111). "
            "July [7](https://arxiv.org/abs/111).\n\n"
            "## Sources\n\n"
            "[3] March paper - Authors A, B\n"
            "   URL: https://arxiv.org/abs/111\n\n"
            "[7] July paper - Authors C, D\n"
            "   URL: https://arxiv.org/abs/111\n\n"
        )
        out = self._enforce(content)
        # Only the winner row survives.
        assert self._displayed_n(out) == [1]
        # Body markers from BOTH rows renumber onto the survivor.
        body = out.split("## Sources")[0]
        assert body.count("[\\[1\\]](https://arxiv.org/abs/111)") == 2

    def test_dedup_preserves_body_marker_for_dropped_row(self):
        """When row A and row B share URL + title (LLM duplicate)
        and the body cites BOTH A and B, all body markers must
        renumber to the surviving row — none get orphaned because
        of the dedup. The renumber step maps the dropped row's
        displayed_n onto the winner's new index.
        """
        content = (
            "# R\n\n"
            "First cite [3](https://example.com/page). "
            "Then cite [7](https://example.com/page). "
            "And one more time [9](https://example.com/page).\n\n"
            "## Sources\n\n"
            "[3] Same Source\n   URL: https://example.com/page\n\n"
            "[7] Same Source\n   URL: https://example.com/page\n\n"
            "[9] Same Source\n   URL: https://example.com/page\n\n"
        )
        out = self._enforce(content)
        # All three body markers collapse to the single surviving [1].
        body = out.split("## Sources")[0]
        assert body.count("[\\[1\\]](https://example.com/page)") == 3
        # Single Sources entry.
        assert self._displayed_n(out) == [1]



class TestBareDoubleBracketCitations:
    """Bare ``[[N]]`` markers (no URL) must be enforced like plain ``[N]``.

    Regression for research 3e9ee493 (2026-08-23): the LLM emitted the
    double-bracket shape without the ``(url)`` tail. Every enforcement
    regex was blind to that form — RENUMBER_HYPERLINK_RE needs the URL,
    RENUMBER_PLAIN_RE's lookarounds skip inside double brackets — so the
    markers survived as plain text, their Sources rows were dropped as
    uncited (rows_rebuilt=2 of 20), and the 参考文献 block came out
    nearly empty while the body showed raw ``[[73]]``.
    """

    DOC = (
        "# 报告\n\n"
        "## 摘要\n"
        "中国生产 [[73]]，墨西哥中转 [74](http://a.onion/x) 与 [[75]]。\n\n"
        "## 一、章节\n"
        "根据 [[73]] 及 [[76]] 的数据。\n\n"
        "## 参考文献\n\n"
        "[73] Source A\n   URL: http://a.onion/x\n\n"
        "[74] Source B\n   URL: http://b.onion/y\n\n"
        "[75] Source C\n   URL: http://c.onion/z\n\n"
        "[76] Source D\n   URL: http://d.onion/w\n"
    )

    def test_bare_double_bracket_hyperlinked_and_rows_kept(self):
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        out = enforce_sources_ascending_and_drop_orphans(self.DOC)
        # Bare [[73]] is hyperlinked to its row's URL, not left raw.
        assert "[[73]]" not in out
        assert "[\\[2\\]](http://a.onion/x)" in out
        # All four cited rows survive the rebuild (uncited-row drop is
        # for rows the body never cites — every row here is cited).
        for title in ("Source A", "Source B", "Source C", "Source D"):
            assert title in out, f"{title} was dropped from 参考文献"
        # Renumbered 1..4 ascending.
        assert "[1] Source" in out
        assert "[4] Source" in out

    def test_bare_double_bracket_orphan_dropped(self):
        """A bare [[N]] with no Sources row for N is deleted."""
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        doc = _make_document(
            "Claim one [[99]]. Claim two [1].",
            "[1] Only Source\n   URL: http://only.onion/",
        )
        out = enforce_sources_ascending_and_drop_orphans(doc)
        assert "[[99]]" not in out
        assert "http://only.onion/" in out

    def test_enforce_idempotent_after_bare_fix(self):
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        # Distinct canonical URLs per row: two body markers sharing one
        # canonical URL across different rows is a pre-existing
        # first-cite-order tie whose reordering is out of scope here.
        doc = (
            "## 摘要\n"
            "甲 [[73]]，乙 [74](http://b.onion/y)，丙 [[75]]。\n\n"
            "## 参考文献\n\n"
            "[73] Source A\n   URL: http://a.onion/x\n\n"
            "[74] Source B\n   URL: http://b.onion/y\n\n"
            "[75] Source C\n   URL: http://c.onion/z\n"
        )
        once = enforce_sources_ascending_and_drop_orphans(doc)
        assert enforce_sources_ascending_and_drop_orphans(once) == once


class TestEnforceIdempotencyInvariant:
    """Enforce must be idempotent across citation shapes and dedup ties.

    Found by fuzzing 2026-08-23 (12000 random docs → 0 failures after
    the fix; previously 768/3000 crashed with KeyError and hundreds
    more were non-idempotent). Root causes fixed:
    - dedup winner not in ordered_rows → KeyError in old_to_new build
    - unmapped markers kept verbatim by renumber_citations (delete_
      unmapped=True now deletes them inside enforce)
    - winner ordered at its own number's position while the rewritten
      body shows its URL at a dropped twin's earlier position
    """

    URLS = [
        "http://a.onion/1",
        "http://a.onion/1",  # canon tie with row 0
        "http://b.onion/2",
        "http://c.onion/3",
    ]

    def _doc(self, parts, rows):
        refs = "\n\n".join(
            f"[{i+1}] T{i}\n   URL: {self.URLS[i]}" for i in rows
        )
        return "x ".join(parts) + "\n\n## Sources\n\n" + refs

    def test_twin_number_positions_the_winner(self):
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        # Canon-a pair (rows 0,1); twin number 1 occurs BEFORE the
        # winner's own number 2. The winner must be ordered at the
        # twin's position so a second pass doesn't reorder.
        doc = self._doc(
            ["[\\[4\\]](http://c.onion/3)", "[[1]]", "[\\[3\\]](http://b.onion/2)", "[2]"],
            [0, 1, 2, 3],
        )
        once = enforce_sources_ascending_and_drop_orphans(doc)
        assert enforce_sources_ascending_and_drop_orphans(once) == once
        # The canon-a source sits at the twin's (2nd) position.
        body = once.split("## Sources")[0]
        assert body.index("a.onion/1") < body.index("b.onion/2")

    def test_uncited_dedup_winner_no_crash(self):
        """Dedup pair where only the loser is body-cited: no KeyError,
        and the citation resolves to the surviving canon row."""
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        doc = self._doc(
            ["[[4]](http://c.onion/3)"], [0, 1, 3]
        )
        out = enforce_sources_ascending_and_drop_orphans(doc)
        assert "T3" in out
        assert "http://c.onion/3" in out

    def test_fuzz_smoke_idempotent(self):
        """Deterministic mini-fuzz (3 seeds x 300 docs)."""
        import random

        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        for seed in (42, 7, 99):
            random.seed(seed)
            for _ in range(300):
                rows = random.sample(range(4), random.randint(2, 4))
                parts = []
                for _ in range(random.randint(1, 4)):
                    r = random.choice(rows)
                    t = random.random()
                    if t < 0.25:
                        parts.append(f"[{r+1}]")
                    elif t < 0.5:
                        parts.append(f"[[{r+1}]]")
                    elif t < 0.75:
                        parts.append(f"[[{r+1}]]({self.URLS[r]})")
                    else:
                        parts.append(f"[{r+1}]({self.URLS[r]})")
                random.shuffle(parts)
                doc = self._doc(parts, rows)
                once = enforce_sources_ascending_and_drop_orphans(doc)
                assert enforce_sources_ascending_and_drop_orphans(
                    once
                ) == once, doc


class TestBracketedLinkText:
    """Link text must render with visible brackets (run 4967de37).

    A bare-number link text ``[3](url)`` renders as a lone "3" in the
    body — the user-visible "引用编号完全没有括号" regression. The
    emission is ``[\\[3\\]](url)``: standard single-bracket markdown
    link whose inner text unescapes to ``[3]``.
    """

    def test_emitted_link_text_has_brackets(self):
        from local_deep_research.text_optimization.citation_formatter import (
            enforce_sources_ascending_and_drop_orphans,
        )

        doc = _make_document(
            "甲 [[73]]，乙 [74](http://b.onion/y)。",
            "[73] A\n   URL: http://a.onion/x\n"
            "[74] B\n   URL: http://b.onion/y",
        )
        out = enforce_sources_ascending_and_drop_orphans(doc)
        body = out.split("## Sources")[0]
        assert "[\\[1\\]](http://a.onion/x)" in body
        assert "[\\[2\\]](http://b.onion/y)" in body
        assert enforce_sources_ascending_and_drop_orphans(out) == out

    def test_image_pipeline_scan_sees_bare_double_bracket(self):
        """relevance.build_citation_index must see bare ``[[N]]`` too —
        the 12e0f3f7 refactor dropped it and the whole image bank
        starved (run 4967de37: ELIGIBLE_BANK total=0)."""
        from local_deep_research.images.relevance import build_citation_index

        md = (
            "## 摘要\n甲 [[3]] 乙 [5](http://b.onion/y) 丙 [7]。\n\n"
            "## 参考文献\n\n"
            "[3] A\n   URL: http://a.onion/x\n\n"
            "[5] B\n   URL: http://b.onion/y\n\n"
            "[7] C\n   URL: http://c.onion/z\n"
        )
        _, s2n, _ = build_citation_index(md, {"findings": []})
        assert sorted(s2n.get(0) or []) == ["3", "5", "7"]
