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


# ---- Stricter token-overlap threshold (宁缺毋滥: drop weak matches) ----

def test_extract_segment_sources_drops_weak_single_token_match():
    """A candidate whose only overlap with the section is a single weak
    token (e.g. "Canton" mentioned in passing) must NOT enter the
    per-section allowed URL set — it would poison the eTLD+1 same-source
    filter for unrelated domains."""
    md = "Canton Tower is a 604-meter landmark in Guangzhou."
    results = {"findings": [
        {"search_results": [
            # Real, dedicated match — must keep
            {"link": "https://a1.ctrip.com/guide/canton-tower",
             "title": "Canton Tower Travel Guide",
             "content": "Canton Tower is a 604-meter landmark in Guangzhou",
             "snippet": "book tours via Ctrip"},
            # Weak mention: only "Canton" overlaps, everything else is
            # unrelated skyscrapers content. Must drop.
            {"link": "https://b.example.com/blog/skyline",
             "title": "Various Skyscrapers Around the World",
             "content": "Tall buildings in many cities",
             "snippet": "general architecture commentary with Canton reference"},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    urls = out[0][2]
    assert "https://a1.ctrip.com/guide/canton-tower" in urls
    assert "https://b.example.com/blog/skyline" not in urls


def test_extract_segment_sources_drops_long_diluted_candidate():
    """A long candidate with only one shared token relative to a short
    section has a low overlap ratio and must be dropped under the
    stricter threshold."""
    md = "## Ctrip\n\nCtrip booking platform."
    results = {"findings": [
        {"search_results": [
            {"link": "https://ctrip.com/about",
             "title": "Ctrip",
             "content": "Ctrip is a large online travel agency platform "
                        "offering flights hotels and tours",
             "snippet": "Ctrip mobile app"},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    # The single candidate has score=2 (Ctrip + Ctrip) but its ratio
    # is ~0.14 because the candidate has many own tokens; under the
    # stricter rule it must be dropped, leaving the section empty
    # (宁缺毋滥).
    assert out[0][2] == []


def test_extract_segment_sources_drops_passing_mention():
    """Paris General News mentioning 'Paris' once must not be linked
    to the Eiffel Tower section — it's not Eiffel Tower content."""
    md = ("## Eiffel Tower\n\nThe Eiffel Tower is a landmark in Paris "
          "built in 1889.")
    results = {"findings": [
        {"search_results": [
            # Real Eiffel content
            {"link": "https://a.com/eiffel",
             "title": "Eiffel Tower Guide",
             "content": "Eiffel Tower Paris France landmark",
             "snippet": "tour Eiffel"},
            # Weak: only "Paris" matches
            {"link": "https://a.com/paris-news",
             "title": "Paris General News",
             "content": "Paris news today",
             "snippet": "general Paris news"},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    urls = out[0][2]
    assert "https://a.com/eiffel" in urls
    assert "https://a.com/paris-news" not in urls
