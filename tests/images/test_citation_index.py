from local_deep_research.images.relevance import build_citation_index


def test_builds_three_mappings():
    md = (
        "## A\n\nText [1] here.\n\n"
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
    # Section 0 (## A) cites [1]; section 1 (## B) is orphan -> [].
    assert section_to_nums[0] == ["1"]
    assert section_to_nums[1] == []
    assert url_to_html["https://example.com/a"] == "[]"
    assert url_to_html["https://example.com/other"] == "[]"


def test_html_mapping_omits_search_results_without_html_content():
    md = "## A\n\n[1].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    results = {"findings": [{"search_results": [
        {"url": "https://x/a"},  # no html_content key
    ]}]}
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html == {}  # missing html_content -> not indexed


def test_empty_results_yields_empty_html_map():
    md = "## A\n\n[1].\n\n## 参考文献\n\n[1] S\n   URL: https://x/a\n"
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, {"findings": []})
    assert num_to_url == {"1": "https://x/a"}
    assert url_to_html == {}
    assert section_to_nums[0] == ["1"]
