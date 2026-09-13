"""B: SearXNG zero-overlap relevance gate.

Root cause (research a4c512fa, 2026-09-13): for rare-entity CJK
queries (阿布力克木·图克尔 ...) upstream engines return unrelated
fallback/ad results (Mattress Firm, Jim Croce ...) which SearXNG
happily aggregates as "25 valid results". A result sharing ZERO
query terms with the query is dropped before it can reach the
citation bank.
"""

from local_deep_research.web_search_engines.engines.search_engine_searxng import (
    filter_zero_overlap_results,
)


def R(title, url, content=""):
    return {
        "title": title,
        "url": url,
        "content": content,
        "engine": "searxng",
        "category": "general",
    }


def test_cjk_query_drops_unrelated_and_keeps_related():
    q = "阿布力克木·图克尔 努里·特克尔 父亲 身份 职业"
    results = [
        R("Mattress Firm | Best Prices", "https://www.mattressfirm.com/"),
        R("PIGAV 朱古力", "https://pigav.ws/"),
        R("努里·特克尔的父亲背景", "https://example.org/nury-turkel"),
    ]
    kept = filter_zero_overlap_results(q, results)
    assert [r["url"] for r in kept] == ["https://example.org/nury-turkel"]


def test_english_query_drops_zero_overlap():
    q = '"Nury Turkel" "Uyghur American Association" president'
    results = [
        R(
            "Nury Turkel - U.S. Commission",
            "https://www.uscirf.gov/nury-turkel",
        ),
        R("DeepSeek Platform", "https://platform.deepseek.com/"),
    ]
    kept = filter_zero_overlap_results(q, results)
    assert [r["url"] for r in kept] == ["https://www.uscirf.gov/nury-turkel"]


def test_url_alone_counts_as_overlap():
    q = "Nury Turkel father"
    results = [
        R("Some site", "https://example.com/Nury-Turkel-bio"),
        R("Unrelated", "https://example.com/other"),
    ]
    kept = filter_zero_overlap_results(q, results)
    assert [r["url"] for r in kept] == ["https://example.com/Nury-Turkel-bio"]


def test_case_insensitive_overlap():
    q = "nury turkel"
    kept = filter_zero_overlap_results(
        q, [R("NURY TURKEL profile", "https://x.example/p")]
    )
    assert len(kept) == 1


def test_no_usable_terms_returns_all():
    # Every term too short / punctuation-only -> no filtering
    q = '"" "a" 的'
    results = [R("Anything", "https://example.com/x")]
    assert filter_zero_overlap_results(q, results) == results


def test_empty_query_or_results_noop():
    results = [R("t", "https://a.b/")]
    assert filter_zero_overlap_results("", results) == results
    assert filter_zero_overlap_results("nury turkel", []) == []
