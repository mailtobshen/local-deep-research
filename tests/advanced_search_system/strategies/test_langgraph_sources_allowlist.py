"""Tests for the post-Sources image-fetch allowlist.

The langgraph strategy's image-fetch was previously unrestricted (every
URL missing html_content got fetched). The 2026-07-30 refactor moved
the fetch to after the Sources block is built, then restricts the
fetch to URLs the LLM actually cited. These tests cover the parsing
helper that derives the allowlist from the formatted report.
"""

from __future__ import annotations

from local_deep_research.advanced_search_system.strategies.langgraph_agent_strategy import (
    _parse_sources_markdown_urls,
)


def test_parse_sources_markdown_urls_returns_cited_urls():
    """The trailing Sources block lists URLs that the LLM cited; return
    them as a set."""
    formatted = (
        "Canton Tower is a 604-meter landmark in Guangzhou.\n\n"
        "## Sources\n\n"
        "[1] Canton Tower — Wikipedia (source nr: 1)\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
        "\n"
        "[2] Shamian (source nr: 2)\n"
        "   URL: https://en.wikipedia.org/wiki/Shamian\n"
        "\n"
    )
    out = _parse_sources_markdown_urls(formatted)
    assert out == {
        "https://en.wikipedia.org/wiki/Canton_Tower",
        "https://en.wikipedia.org/wiki/Shamian",
    }


def test_parse_sources_markdown_urls_skips_rows_without_url():
    """A row with an empty ``URL:`` line is dropped — the caller
    cannot fetch a URL it does not know about."""
    formatted = (
        "## Sources\n\n"
        "[1] Has URL\n"
        "   URL: https://example.com/a\n"
        "\n"
        "[2] Title without URL\n"
        "   URL: \n"
        "\n"
    )
    out = _parse_sources_markdown_urls(formatted)
    assert out == {"https://example.com/a"}


def test_parse_sources_markdown_urls_empty_input():
    """No markdown → no URLs."""
    assert _parse_sources_markdown_urls("") == set()


def test_parse_sources_markdown_urls_no_sources_section():
    """Markdown without a Sources heading → no URLs. Common when the
    LLM produced no citations."""
    md = "## A\n\nbody with no [1] markers.\n## B\n\nmore body.\n"
    assert _parse_sources_markdown_urls(md) == set()


def test_parse_sources_markdown_urls_english_sources_heading():
    """English ``## Sources`` heading is recognised (the upstream
    scanner in citation_formatter handles this)."""
    formatted = (
        "## Sources\n\n"
        "[1] A\n"
        "   URL: https://example.com/a\n"
    )
    out = _parse_sources_markdown_urls(formatted)
    assert out == {"https://example.com/a"}


def test_parse_sources_markdown_urls_uses_canonical_form():
    """``format_links_to_markdown`` runs ``canonical_url_key`` before
    writing the URL: line, so the helper sees canonical URLs. This
    test pins the contract so the caller's intersect with
    ``canonical_url_key``-normalised discovered URLs works."""
    formatted = (
        "## Sources\n\n"
        "[1] A\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    out = _parse_sources_markdown_urls(formatted)
    # No trailing slash, no utm, no fragment.
    assert "https://en.wikipedia.org/wiki/Canton_Tower" in out
    assert all("?" not in u for u in out)
    assert all(u.endswith("/Canton_Tower") for u in out)


def test_parse_sources_markdown_urls_deduplicates_rows_pointing_to_same_url():
    """Two rows with the same URL collapse to one entry in the set."""
    formatted = (
        "## Sources\n\n"
        "[1] First listing\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
        "\n"
        "[2] Alternate listing\n"
        "   URL: https://en.wikipedia.org/wiki/Canton_Tower\n"
    )
    out = _parse_sources_markdown_urls(formatted)
    assert out == {"https://en.wikipedia.org/wiki/Canton_Tower"}
