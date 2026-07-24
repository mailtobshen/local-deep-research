"""Tests for extract_segment_sources (Task 1: segment ↔ source URL matching).

Each `## ` section in the cleaned markdown should be paired with the
top-N search-result URLs whose title/content most closely overlap with
the section's heading + body. Sections with no overlap inherit the
previous section's allow-list so the LLM still has authoritative URLs
to cite when an orphan section appears between matched ones.
"""

from __future__ import annotations

from local_deep_research.images.postprocessing import extract_segment_sources


def test_extract_segment_sources_chinese_alignment():
    md = "## 鼓浪嶼\n\n厦门鼓浪屿介绍。\n\n## 越秀公园\n\n广州越秀公园介绍。\n"
    results = {
        "findings": [
            {"search_results": [
                {"link": "https://xiamen-travel.com/places", "title": "厦门鼓浪屿",
                 "content": "鼓浪屿是厦门著名景点", "snippet": "鼓浪屿"},
                {"link": "https://gzdaily.com/places", "title": "广州越秀公园",
                 "content": "越秀公园在广州", "snippet": "越秀公园"},
            ]}
        ]
    }
    out = extract_segment_sources(md, results)
    assert [seg[2] for seg in out] == [
        ["https://xiamen-travel.com/places"],
        ["https://gzdaily.com/places"],
    ]


def test_extract_segment_sources_inherits_when_no_match():
    md = "## 鼓浪嶼\n\nA\n\n## Foo\n\nB\n"
    # Section 0 actually matches a candidate so the inherited allow-list
    # is non-empty; section 1 has no match and must inherit.
    results = {"findings": [
        {"search_results": [
            {"link": "https://xiamen-travel.com", "title": "鼓浪嶼",
             "content": "鼓浪屿介绍", "snippet": "鼓浪屿"},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    assert out[0][2] == ["https://xiamen-travel.com"]
    assert out[1][2] == ["https://xiamen-travel.com"]


def test_extract_segment_sources_no_results_returns_empty():
    md = "## A\n\nbody"
    assert extract_segment_sources(md, {}) == []


def test_extract_segment_sources_uses_top_n_per_segment():
    md = "## 鼓浪嶼\n\n厦门鼓浪屿\n"
    results = {"findings": [
        {"search_results": [
            {"link": "https://xiamen-travel.com", "title": "鼓浪屿", "content": "鼓浪屿", "snippet": ""},
            {"link": "https://gzdaily.com", "title": "广州", "content": "广州", "snippet": ""},
            {"link": "https://others.com", "title": "Other", "content": "Other", "snippet": ""},
        ]}
    ]}
    out = extract_segment_sources(md, results, top_n=1)
    assert out[0][2] == ["https://xiamen-travel.com"]
