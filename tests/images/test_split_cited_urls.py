"""Verify _split_cited_urls partition logic (fix #3 from
docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md).

The helper splits ``cited_urls`` into (covered, gap) using the same
``url_to_html`` dict that ``build_citation_index`` produces. The
invariant ``len(covered) + len(gap) == len(cited_urls)`` must always
hold, replacing the pre-#3 ``already_html + to_fetch != cited`` bug.
"""


def test_partition_when_url_to_html_empty():
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    cited = {"https://a", "https://b", "https://c"}
    url_to_html: dict[str, str] = {}
    covered, gap = _split_cited_urls(cited, url_to_html)
    assert covered == set()
    assert sorted(gap) == sorted(cited)
    assert len(covered) + len(gap) == len(cited)


def test_partition_when_all_covered():
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    cited = {"https://a", "https://b"}
    url_to_html = {"https://a": "html-a", "https://b": "html-b"}
    covered, gap = _split_cited_urls(cited, url_to_html)
    assert covered == cited
    assert gap == []
    assert len(covered) + len(gap) == len(cited)


def test_partition_mixed():
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    cited = {"https://a", "https://b", "https://c"}
    # Only a and c have html; b needs fetch
    url_to_html = {
        "https://a": "html-a",
        "https://c": "html-c",
        "https://unrelated": "html-unrelated",  # ignored
    }
    covered, gap = _split_cited_urls(cited, url_to_html)
    assert covered == {"https://a", "https://c"}
    assert gap == ["https://b"]
    assert len(covered) + len(gap) == len(cited)


def test_partition_when_url_to_html_has_extra_unrelated_urls():
    """url_to_html can contain URLs not in cited_urls; they must be ignored."""
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    cited = {"https://cited-only"}
    url_to_html = {
        "https://cited-only": "ok",
        "https://extra-1": "x",
        "https://extra-2": "y",
    }
    covered, gap = _split_cited_urls(cited, url_to_html)
    assert covered == {"https://cited-only"}
    assert gap == []
    assert len(covered) + len(gap) == len(cited)


def test_invariant_holds_for_empty_cited():
    """Trivial case: no citations, no work."""
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    covered, gap = _split_cited_urls(set(), {})
    assert covered == set()
    assert gap == []
    assert len(covered) + len(gap) == 0


def test_partition_uses_url_to_html_truth_not_search_results():
    """Pre-#3 the deferred pass iterated search_results[] only and
    produced self-contradicting counts. _split_cited_urls takes
    url_to_html directly so the partition always agrees with what
    build_citation_index saw. This is the core invariant fix.
    """
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    # Simulates: 5 subsections each fetched a URL, all 5 are cited,
    # url_to_html reflects all 5 (post-#1+#6).
    cited = {f"https://x/{i}" for i in range(5)}
    url_to_html = {f"https://x/{i}": f"html-{i}" for i in range(5)}

    covered, gap = _split_cited_urls(cited, url_to_html)
    assert covered == cited
    assert gap == []
    assert len(covered) + len(gap) == len(cited)


def test_partition_gap_dedupes_inputs():
    """If cited_urls contains the same URL twice (shouldn't happen but
    defend anyway), gap stays a list and doesn't duplicate either."""
    from local_deep_research.web.services.research_service import (
        _split_cited_urls,
    )

    # cited_urls is a set so it dedupes by definition; this just
    # documents the contract.
    cited = {"https://x"}
    covered, gap = _split_cited_urls(cited, {})
    assert len(gap) == 1