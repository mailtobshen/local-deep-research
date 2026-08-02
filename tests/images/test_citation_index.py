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


def test_empty_results_yields_empty_html_map():
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, {"findings": []})
    assert num_to_url == {"1": "https://x/a"}
    assert url_to_html == {}
    assert section_to_nums[0] == ["1"]
