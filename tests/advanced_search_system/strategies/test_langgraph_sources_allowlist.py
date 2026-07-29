"""Tests for the post-Sources image-fetch allowlist.

The langgraph strategy's image-fetch was previously unrestricted (every
URL missing html_content got fetched). The 2026-07-30 refactor moved
the fetch to after the Sources block is built, then restricts the
fetch to URLs the LLM actually cited. These tests cover the parsing
helper that derives the allowlist from the formatted report, and the
URL-selection helper that decides which URLs to fetch.
"""

from __future__ import annotations

from local_deep_research.advanced_search_system.strategies.langgraph_agent_strategy import (
    _parse_sources_markdown_urls,
    _select_urls_to_fetch,
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


# ---- _select_urls_to_fetch (cited-URL-driven fetch) ----

def test_select_urls_to_fetch_cited_urls_not_in_search_results_are_fetched():
    """A URL the LLM cited but the agent never searched is still
    fetched — that is the whole point of moving the fetch to after
    the Sources block was built. The previous implementation
    iterated ``all_search_results`` and silently dropped such URLs
    if they had no search-result row."""
    out = _select_urls_to_fetch(
        all_search_results=[],
        allowed_urls={
            "https://a1.ctrip.com/guide/canton-tower",
            "https://b.ctrip.com/photo-of-tower",
        },
    )
    assert set(out) == {
        "https://a1.ctrip.com/guide/canton-tower",
        "https://b.ctrip.com/photo-of-tower",
    }


def test_select_urls_to_fetch_cited_with_existing_html_content_is_skipped():
    """A cited URL whose html_content is already populated in
    search results is skipped — the agent's earlier fetch
    suffices."""
    out = _select_urls_to_fetch(
        all_search_results=[
            {"url": "https://a1.ctrip.com/x", "html_content": "<html/>"},
        ],
        allowed_urls={
            "https://a1.ctrip.com/x",
            "https://b.ctrip.com/y",
        },
    )
    assert out == ["https://b.ctrip.com/y"]


def test_select_urls_to_fetch_cited_with_empty_html_content_is_fetched():
    """A cited URL whose html_content is an empty string is still
    fetched — empty is not the same as populated."""
    out = _select_urls_to_fetch(
        all_search_results=[
            {"url": "https://a1.ctrip.com/x", "html_content": ""},
        ],
        allowed_urls={"https://a1.ctrip.com/x"},
    )
    assert out == ["https://a1.ctrip.com/x"]


def test_select_urls_to_fetch_trailing_slash_normalisation():
    """``https://a1.ctrip.com/x/`` and ``https://a1.ctrip.com/x``
    normalise to the same lookup key; html_content populated under
    one form blocks the other."""
    out = _select_urls_to_fetch(
        all_search_results=[
            {"url": "https://a1.ctrip.com/x/", "html_content": "<html/>"},
        ],
        allowed_urls={"https://a1.ctrip.com/x"},
    )
    assert out == []


def test_select_urls_to_fetch_legacy_none_fetches_every_missing():
    """``allowed_urls=None`` preserves the legacy behaviour: every
    URL in ``all_search_results`` that is missing html_content is
    fetched."""
    out = _select_urls_to_fetch(
        all_search_results=[
            {"url": "https://x.com", "html_content": ""},
            {"url": "https://y.com", "html_content": "<html/>"},
            {"url": "https://z.com"},
        ],
        allowed_urls=None,
    )
    assert out == ["https://x.com", "https://z.com"]


def test_select_urls_to_fetch_dedupes_allowed_set():
    """A duplicated entry in the allowed set is collapsed to one."""
    out = _select_urls_to_fetch(
        all_search_results=[],
        allowed_urls={"https://x.com", "https://x.com", "https://y.com"},
    )
    # Set iteration order is not deterministic; assert membership.
    assert set(out) == {"https://x.com", "https://y.com"}
    assert len(out) == 2


def test_select_urls_to_fetch_empty_allowed_set_short_circuits_via_caller():
    """``_select_urls_to_fetch`` itself returns an empty list when
    allowed_urls is empty — the caller is expected to short-circuit
    before calling (the existing check is
    ``if allowed_urls is not None and not allowed_urls: return``).
    Verifying the helper's behaviour independently documents the
    contract."""
    out = _select_urls_to_fetch(
        all_search_results=[{"url": "https://x.com"}],
        allowed_urls=set(),
    )
    assert out == []
