"""
Tests for utilities/search_utilities.py

Tests cover:
- remove_think_tags function
- extract_links_from_search_results function
- format_links_to_markdown function
- format_findings function
"""


class TestRemoveThinkTags:
    """Tests for remove_think_tags function."""

    def test_removes_paired_think_tags(self):
        """Test that paired think tags are removed."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "Hello <think>This is internal</think> World"
        result = remove_think_tags(text)
        assert result == "Hello  World"

    def test_removes_multiline_think_tags(self):
        """Test that multiline think tags are removed."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "Start <think>\nLine 1\nLine 2\n</think> End"
        result = remove_think_tags(text)
        assert result == "Start  End"

    def test_removes_orphaned_opening_tag(self):
        """Test that orphaned opening think tags are removed."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "Hello <think>world"
        result = remove_think_tags(text)
        assert result == "Hello world"

    def test_removes_orphaned_closing_tag(self):
        """Test that orphaned closing think tags are removed."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "Hello</think> world"
        result = remove_think_tags(text)
        assert result == "Hello world"

    def test_strips_whitespace(self):
        """Test that result is stripped."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "  Hello  "
        result = remove_think_tags(text)
        assert result == "Hello"

    def test_text_without_tags_unchanged(self):
        """Test that text without think tags is unchanged."""
        from local_deep_research.utilities.search_utilities import (
            remove_think_tags,
        )

        text = "Hello World"
        result = remove_think_tags(text)
        assert result == "Hello World"


class TestExtractLinksFromSearchResults:
    """Tests for extract_links_from_search_results function."""

    def test_extracts_links_from_results(self):
        """Test extracting links from search results."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        results = [
            {
                "title": "Result 1",
                "link": "https://example.com/1",
                "index": "1",
            },
            {
                "title": "Result 2",
                "link": "https://example.com/2",
                "index": "2",
            },
        ]

        links = extract_links_from_search_results(results)

        assert len(links) == 2
        assert links[0]["title"] == "Result 1"
        assert links[0]["url"] == "https://example.com/1"
        assert links[0]["index"] == "1"

    def test_returns_empty_for_empty_input(self):
        """Test returns empty list for empty input."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        assert extract_links_from_search_results([]) == []
        assert extract_links_from_search_results(None) == []

    def test_handles_missing_title(self):
        """Test handles results without title."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        results = [{"link": "https://example.com"}]
        links = extract_links_from_search_results(results)

        # Should not include results without title
        assert len(links) == 0

    def test_handles_missing_link(self):
        """Test handles results without link."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        results = [{"title": "No Link Result"}]
        links = extract_links_from_search_results(results)

        # Should not include results without link
        assert len(links) == 0

    def test_strips_whitespace_from_values(self):
        """Test that whitespace is stripped from values."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        results = [
            {
                "title": "  Padded Title  ",
                "link": "  https://example.com  ",
                "index": " 1 ",
            }
        ]

        links = extract_links_from_search_results(results)

        assert links[0]["title"] == "Padded Title"
        assert links[0]["url"] == "https://example.com"
        assert links[0]["index"] == "1"

    def test_handles_none_values(self):
        """Test handles None values gracefully."""
        from local_deep_research.utilities.search_utilities import (
            extract_links_from_search_results,
        )

        results = [{"title": None, "link": None}]
        links = extract_links_from_search_results(results)

        assert len(links) == 0


class TestFormatLinksToMarkdown:
    """Tests for format_links_to_markdown function."""

    def test_formats_single_link(self):
        """Test formatting a single link."""
        from local_deep_research.utilities.search_utilities import (
            format_links_to_markdown,
        )

        links = [
            {"title": "Example", "url": "https://example.com", "index": "1"}
        ]

        result = format_links_to_markdown(links)

        assert "Example" in result
        assert "https://example.com" in result

    def test_formats_multiple_links(self):
        """Test formatting multiple links."""
        from local_deep_research.utilities.search_utilities import (
            format_links_to_markdown,
        )

        links = [
            {"title": "First", "url": "https://first.com", "index": "1"},
            {"title": "Second", "url": "https://second.com", "index": "2"},
        ]

        result = format_links_to_markdown(links)

        assert "First" in result
        assert "Second" in result

    def test_deduplicates_urls(self):
        """Test that duplicate URLs are deduplicated."""
        from local_deep_research.utilities.search_utilities import (
            format_links_to_markdown,
        )

        links = [
            {"title": "First", "url": "https://same.com", "index": "1"},
            {"title": "Duplicate", "url": "https://same.com", "index": "2"},
        ]

        result = format_links_to_markdown(links)

        # Should only appear once
        assert result.count("https://same.com") == 1

    def test_returns_empty_for_empty_input(self):
        """Test returns empty for no links."""
        from local_deep_research.utilities.search_utilities import (
            format_links_to_markdown,
        )

        result = format_links_to_markdown([])
        assert result == ""

    def test_handles_link_key_fallback(self):
        """Test handles 'link' key as fallback for 'url'."""
        from local_deep_research.utilities.search_utilities import (
            format_links_to_markdown,
        )

        links = [{"title": "Test", "link": "https://example.com", "index": "1"}]

        result = format_links_to_markdown(links)

        assert "https://example.com" in result


class TestFormatFindings:
    """Tests for format_findings function."""

    def test_formats_empty_findings(self):
        """Test formatting with empty findings."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        result = format_findings([], "Synthesized content", {})

        assert "Synthesized content" in result

    def test_includes_synthesized_content(self):
        """Test that synthesized content is included."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        result = format_findings([], "My analysis of the data", {})

        assert "My analysis of the data" in result

    def test_includes_questions_by_iteration(self):
        """Test that questions are included by iteration."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        questions = {1: ["Question 1", "Question 2"], 2: ["Question 3"]}

        result = format_findings([], "Content", questions)

        assert "SEARCH QUESTIONS BY ITERATION" in result
        assert "Question 1" in result
        assert "Question 2" in result
        assert "Question 3" in result
        assert "Iteration 1" in result
        assert "Iteration 2" in result

    def test_includes_detailed_findings(self):
        """Test that detailed findings are included."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        findings = [
            {
                "phase": "Initial Search",
                "content": "Found relevant information.",
                "search_results": [],
            }
        ]

        result = format_findings(findings, "Summary", {})

        assert "DETAILED FINDINGS" in result
        assert "Initial Search" in result
        assert "Found relevant information" in result

    def test_formats_follow_up_phase_with_question(self):
        """Test formatting follow-up phase shows question."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        findings = [
            {
                "phase": "Follow-up Iteration 1.1",
                "content": "Follow-up content",
                "search_results": [],
            }
        ]
        questions = {1: ["First follow-up question"]}

        result = format_findings(findings, "Summary", questions)

        assert "First follow-up question" in result

    def test_includes_sources_in_findings(self):
        """Test that sources are included in findings."""
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        findings = [
            {
                "phase": "Search",
                "content": "Content",
                "search_results": [
                    {
                        "title": "Source 1",
                        "link": "https://source1.com",
                        "index": "1",
                    }
                ],
            }
        ]

        result = format_findings(findings, "Summary", {})

        assert "ALL SOURCES" in result or "source1.com" in result


class TestEnsureMarkdownBlockBoundaries:
    """Tests for the markdown block-boundary normaliser.

    CommonMark requires a blank line between a paragraph and a
    following list / heading / blockquote. LLMs routinely omit the
    blank line, which makes marked treat the next ``*`` / ``#`` as
    literal paragraph characters (research d7bdf3b4, 2026-08-27).
    """

    def test_idempotent_on_already_normalized(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "# Title\n\nParagraph.\n\n* item\n* item\n\n### Next\n"
        assert _ensure_markdown_block_boundaries(md) == md

    def test_inserts_blank_before_unordered_list_after_paragraph(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "数字图传系统正在逐渐普及。\n* **Caddx/Walksnail Avatar HD**：极低 [\\[9\\]](https://rcdrone.top/)\n"
        result = _ensure_markdown_block_boundaries(md)
        # The * **Caddx...** line must be preceded by a blank line
        lines = result.split("\n")
        list_idx = next(
            i for i, line in enumerate(lines) if line.startswith("* ")
        )
        assert lines[list_idx - 1] == ""

    def test_inserts_blank_before_heading_after_paragraph(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "last list item。\n### 2.5 摄像头"
        result = _ensure_markdown_block_boundaries(md)
        lines = result.split("\n")
        heading_idx = next(
            i for i, line in enumerate(lines) if line.startswith("###")
        )
        assert lines[heading_idx - 1] == ""

    def test_inserts_blank_before_ordered_list(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "paragraph\n1. first\n2. second"
        result = _ensure_markdown_block_boundaries(md)
        assert "\n\n1." in result

    def test_does_not_split_blockquote_continuation(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "> first line\n> continuation\n# heading"
        result = _ensure_markdown_block_boundaries(md)
        # Blockquote continuation should be preserved verbatim. The
        # previous line is a blockquote (opener ``>``), so the heading
        # opener ``#`` does not insert a blank line either (it's a
        # continuation of the blockquote in our heuristic — see
        # ``opener_re.match(prev)`` guard).
        # This is a deliberate trade-off: detecting the END of a
        # blockquote requires tracking block depth, which this
        # single-pass function does not do. The benefit: a true
        # continuation like ``> a\n> b\n1. `` is preserved; the cost:
        # ``> a\n# heading`` is left without a blank line in this
        # edge case. In practice, LLMs don't emit blockquote-then-
        # heading without a blank line.
        lines = result.split("\n")
        assert lines[0] == "> first line"
        assert lines[1] == "> continuation"
        # No blank inserted between blockquote continuation and heading.
        assert lines[2] == "# heading"

    def test_does_not_modify_fenced_code_block_content(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = (
            "```python\n"
            "print('hello')\n"
            "1. this is not a list\n"
            "print('world')\n"
            "```\n"
            "\n"
            "paragraph\n"
            "1. real list item\n"
        )
        result = _ensure_markdown_block_boundaries(md)
        # Inside code block: no blank line inserted before "1. this"
        # After code block + paragraph: real "1. real list item" should
        # be preceded by a blank line (paragraph ends the preceding
        # block).
        assert "1. this is not a list" in result
        # Find the "1. real list item" line and verify preceding blank
        lines = result.split("\n")
        real_idx = lines.index("1. real list item")
        assert lines[real_idx - 1] == ""

    def test_empty_string(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        assert _ensure_markdown_block_boundaries("") == ""

    def test_no_op_on_input_without_blocks(self):
        from local_deep_research.utilities.search_utilities import (
            _ensure_markdown_block_boundaries,
        )

        md = "just a paragraph\nwith a soft line break\nstill a paragraph"
        assert _ensure_markdown_block_boundaries(md) == md


class TestFormatFindingsBlockBoundaryIntegration:
    """format_findings must pass synthesized_content through
    _ensure_markdown_block_boundaries so the markdown saved to the
    database is correctly normalised.
    """

    def test_d7bdf3b4_scenario(self):
        """The exact bug from research d7bdf3b4 (2026-08-27):
        figure + plain paragraph + bullet list without blank lines
        between them. Before the fix this rendered as literal
        markdown text instead of a proper ``<ul><li>``.
        """
        from local_deep_research.utilities.search_utilities import (
            format_findings,
        )

        synthesized = (
            "<figure><img src=\"x\" /></figure>\n"
            "数字图传系统正在逐渐普及，替代传统的模拟系统。\n"
            "* **Caddx/Walksnail Avatar HD**：极低 [\\[9\\]](https://rcdrone.top/)\n"
            "* **DJI O4 Pro / O3 Air Unit**：较高 [\\[10\\]](https://makerworld.com/)\n"
            "* **Walksnail Avatar HD Mini VTX V3**：低重量 [\\[11\\]](https://drones.bg/)\n"
            "\n"
            "### 2.5 摄像头（Camera）\n"
        )
        result = format_findings([], synthesized, {})
        # Verify the list items and heading each have a blank-line
        # separator in the formatted output.
        lines = result.split("\n")
        first_list_idx = next(
            i for i, line in enumerate(lines) if line.startswith("* ")
        )
        assert lines[first_list_idx - 1] == "", (
            "First list item not preceded by blank line: "
            f"prev={lines[first_list_idx - 1]!r}"
        )
        heading_idx = next(
            i for i, line in enumerate(lines) if line.startswith("###")
        )
        assert lines[heading_idx - 1] == "", (
            "Heading not preceded by blank line: "
            f"prev={lines[heading_idx - 1]!r}"
        )


class TestLanguageCodeMap:
    """Tests for LANGUAGE_CODE_MAP constant."""

    def test_contains_common_languages(self):
        """Test that map contains common languages."""
        from local_deep_research.utilities.search_utilities import (
            LANGUAGE_CODE_MAP,
        )

        assert LANGUAGE_CODE_MAP["english"] == "en"
        assert LANGUAGE_CODE_MAP["french"] == "fr"
        assert LANGUAGE_CODE_MAP["german"] == "de"
        assert LANGUAGE_CODE_MAP["spanish"] == "es"
        assert LANGUAGE_CODE_MAP["italian"] == "it"
        assert LANGUAGE_CODE_MAP["japanese"] == "ja"
        assert LANGUAGE_CODE_MAP["chinese"] == "zh"
