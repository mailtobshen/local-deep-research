from local_deep_research.images.relevance import build_citation_index


def test_builds_three_mappings():
    """Real production format: body cites as [[N]](url) links; the
    References block rows are single-bracket ``[N] Title\n URL:`` lines."""
    md = (
        "## A\n\nText [[1]](https://example.com/a) here.\n\n"
        "## B\n\nNo citation.\n\n"
        "## 参考文献\n\n"
        "[1] Source A\n   URL: https://example.com/a\n"
    )
    results = {
        "findings": [
            {"search_results": [
                {"url": "https://example.com/a", "html_content": "[]"},
                {"url": "https://example.com/other", "html_content": "[]"},
            ]}
        ]
    }
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, results)

    assert num_to_url == {"1": "https://example.com/a"}
    # Section 0 (## A) cites [[1]]; section 1 (## B) is orphan -> [].
    assert section_to_nums[0] == ["1"]
    assert section_to_nums[1] == []
    assert url_to_html["https://example.com/a"] == "[]"
    assert url_to_html["https://example.com/other"] == "[]"


def test_all_links_of_system_merged_into_url_to_html():
    """Detailed mode: _results is wiped by collector.reset() between
    subsections, but all_links_of_system is preserved. The deferred pass
    passes the cumulative list in results["all_links_of_system"]; build
    must surface its html_content through url_to_html.

    Without this fix (#1+#6) the deferred pass only saw the last
    subsection's records and to_fetch dropped to 0 with html_covered=2
    even when many subsections had fetched cited URLs (e2ec21ad 2026-08-05).
    """
    md = (
        "## A\n\nCites [[1]](https://sub1.example.com/a) and "
        "[[2]](https://sub2.example.com/b).\n\n"
        "## 参考文献\n\n"
        "[1] One\n   URL: https://sub1.example.com/a\n"
        "[2] Two\n   URL: https://sub2.example.com/b\n"
    )
    # search_results only contains the LAST subsection's records
    # (sub2 fetched in the final subsection; sub1 was wiped by reset).
    results = {
        "findings": [
            {"search_results": [
                {"url": "https://sub2.example.com/b", "html_content": "[]"},
            ]}
        ],
        # all_links_of_system carries BOTH subsections' html_content
        "all_links_of_system": [
            {
                "link": "https://sub1.example.com/a",
                "url": "https://sub1.example.com/a",
                "html_content": "[sub1 fetched earlier]",
            },
            {
                "link": "https://sub2.example.com/b",
                "url": "https://sub2.example.com/b",
                "html_content": "[]",
            },
        ],
    }
    _, _, url_to_html = build_citation_index(md, results)
    # sub1 comes from all_links_of_system; sub2 from either source.
    assert url_to_html["https://sub1.example.com/a"] == "[sub1 fetched earlier]"
    assert url_to_html["https://sub2.example.com/b"] == "[]"


def test_all_links_of_system_does_not_overwrite_existing():
    """If both search_results and all_links_of_system have the same URL,
    search_results wins (it's the freshest data path)."""
    md = (
        "## A\n\n[[1]](https://x.example.com)\n\n"
        "## 参考文献\n\n[1] X\n   URL: https://x.example.com\n"
    )
    results = {
        "findings": [
            {"search_results": [
                {"url": "https://x.example.com", "html_content": "fresh"},
            ]}
        ],
        "all_links_of_system": [
            {
                "link": "https://x.example.com",
                "html_content": "stale",
            }
        ],
    }
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html["https://x.example.com"] == "fresh"


def test_all_links_of_system_absent_keeps_old_behavior():
    """Without the field, behavior is unchanged from pre-#1+#6."""
    md = (
        "## A\n\n[[1]](https://x.example.com)\n\n"
        "## 参考文献\n\n[1] X\n   URL: https://x.example.com\n"
    )
    results = {
        "findings": [
            {"search_results": [
                {"url": "https://x.example.com", "html_content": "ok"},
            ]}
        ]
    }
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html == {"https://x.example.com": "ok"}


def test_all_links_of_system_empty_list_keeps_old_behavior():
    """An empty list is treated the same as absent."""
    md = (
        "## A\n\n[[1]](https://x.example.com)\n\n"
        "## 参考文献\n\n[1] X\n   URL: https://x.example.com\n"
    )
    results = {
        "findings": [],
        "all_links_of_system": [],
    }
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html == {}


def test_plain_double_bracket_citation_without_link():
    """[[N]] without a following (url) is also a valid body citation."""
    md = "## A\n\nText [[1]] here.\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    num_to_url, section_to_nums, _ = build_citation_index(md, {"findings": []})
    assert section_to_nums[0] == ["1"]


def test_single_bracket_body_citation_still_parsed():
    """Single-bracket [N] in the body (pre-link format) is compatible."""
    md = "## A\n\nText [1] here.\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    _, section_to_nums, _ = build_citation_index(md, {"findings": []})
    assert section_to_nums[0] == ["1"]


def test_references_block_rows_not_scanned_as_body_citations():
    """Rows in the References block ([1, 1224] Title / [2] Title) are
    parsed into num_to_url but must NOT leak into a section's body nums —
    otherwise the final section swallows every reference."""
    md = (
        "## A\n\nText [[1]] here.\n\n"
        "## 参考文献\n\n"
        "[1, 1224] Row A\n   URL: https://example.com/a\n"
        "[2] Row B\n   URL: https://example.com/b\n"
    )
    num_to_url, section_to_nums, _ = build_citation_index(md, {"findings": []})
    # References rows still parse into the citation map.
    assert num_to_url["1"] == "https://example.com/a"
    assert num_to_url["1224"] == "https://example.com/a"
    assert num_to_url["2"] == "https://example.com/b"
    # The References block section itself cites nothing.
    last_sidx = max(section_to_nums)
    assert section_to_nums[last_sidx] == []
    # And the body section keeps only its real citation.
    assert section_to_nums[0] == ["1"]


def test_html_mapping_omits_search_results_without_html_content():
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    results = {"findings": [{"search_results": [
        {"url": "https://x/a"},  # no html_content key
    ]}]}
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html == {}  # missing html_content -> not indexed


def test_html_mapping_reads_production_link_key():
    """Production search_results are SearXNG items keyed "link" (see
    web_search_engines/engines/full_search.py); url_to_html must
    still resolve them."""
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    results = {"findings": [{"search_results": [
        {"link": "https://x/a", "html_content": "[]"},
    ]}]}
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html["https://x/a"] == "[]"


def test_empty_results_yields_empty_html_map():
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, {"findings": []})
    assert num_to_url == {"1": "https://x/a"}
    assert url_to_html == {}
    assert section_to_nums[0] == ["1"]
