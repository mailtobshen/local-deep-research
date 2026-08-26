"""Unified citation numbering: LLM view == Sources block rows.

Root cause (research 496944b7, 2026-08-25): the citation handler's
``_create_documents`` numbers sources ``i + nr_of_links + 1`` over the
RAW result list (179 entries, duplicates included), while
``format_links_to_markdown`` numbers Sources rows by the original
``link['index']`` unions AFTER canonical-URL dedup (96 rows). The two
numbering spaces are misaligned, so every inline ``[N]`` the LLM wrote
fails enforce's orphan-drop URL match — 11 body citations all died,
``rebuilt_empty rows_in=96``.

Fix (path B — numbering authority moves before synthesis):

1. ``assign_citation_numbers(search_results)`` — filter entries whose
   canonical URL is empty, dedup by canonical URL preserving
   first-seen order (merging html_content/full_content first-non-empty),
   assign continuous 1..K, write ``result['index'] = str(n)`` back onto
   the surviving entries.
2. ``_finalize`` calls it BEFORE ``analyze_followup`` (the documents
   prompt and the Sources block then share one numbering), and passes
   the numbered entries to ``format_links_to_markdown`` via a new
   ``numbered=True`` mode that emits rows ``[n] Title (source nr: n)``
   with n = 1..K continuous ascending (no ``[1, 1224]`` comma groups).
3. ``_used_nums_in_body`` learns the escaped hyperlink form
   ``[\\[73\\]](url)`` so the sanitizer stops misjudging bodies that
   use cite_link_text output.
"""

import re

import pytest

from local_deep_research.utilities.search_utilities import (
    assign_citation_numbers,
    format_links_to_markdown,
)
from local_deep_research.images.reference_sanitizer import _used_nums_in_body


def _mk_results():
    """Mimic research 496944b7: 6 raw results with the pathologies.

    - r0/r1 duplicate canonical URL (trailing-slash drift), html only on r1
    - r2 empty URL (must not enter the numbering space)
    - r3 unique
    - r4/r5 duplicate canonical (query-param drift), html on both
    """
    return [
        {"title": "Alpha", "url": "http://a.onion/x", "snippet": "s0"},
        {
            "title": "Alpha",
            "url": "http://a.onion/x/",
            "snippet": "s1",
            "html_content": "<b>alpha html</b>",
        },
        {"title": "NoUrl", "url": "", "snippet": "s2"},
        {"title": "Gamma", "url": "http://c.onion/z", "snippet": "s3"},
        {
            "title": "Delta",
            "url": "http://d.onion/p",
            "snippet": "s4",
            "html_content": "<i>delta1</i>",
        },
        {
            "title": "Delta",
            "url": "http://d.onion/p/",
            "snippet": "s5",
            "html_content": "<i>delta2</i>",
        },
    ]


class TestAssignCitationNumbers:
    def test_continuous_ascending_from_1(self):
        results = _mk_results()
        numbered = assign_citation_numbers(results)
        idxs = [int(r["index"]) for r in numbered]
        assert idxs == [1, 2, 3], f"expected [1, 2, 3], got {idxs}"

    def test_empty_url_excluded(self):
        numbered = assign_citation_numbers(_mk_results())
        assert all(r.get("url") for r in numbered)
        assert "NoUrl" not in [r["title"] for r in numbered]

    def test_dedup_keeps_first_merges_html(self):
        numbered = assign_citation_numbers(_mk_results())
        # alpha pair: first-seen entry survives but carries the html
        # that lived only on its duplicate
        alpha = [r for r in numbered if r["title"] == "Alpha"][0]
        assert alpha.get("html_content") == "<b>alpha html</b>"
        # delta pair: first non-empty wins (already on the first entry)
        delta = [r for r in numbered if r["title"] == "Delta"][0]
        assert delta.get("html_content") == "<i>delta1</i>"
        # 6 raw − 1 empty − 2 dupes = 3 survivors
        assert len(numbered) == 3

    def test_original_list_untouched_semantics(self):
        """The helper returns the surviving entries; caller uses the
        return value (the input list is not required to be mutated
        in place, but survivors must be the same dict objects so the
        strategy can still pass them around)."""
        results = _mk_results()
        numbered = assign_citation_numbers(results)
        assert all(r in results for r in numbered)


class TestFormatLinksNumbered:
    def test_rows_continuous_no_comma_groups(self):
        results = assign_citation_numbers(_mk_results())
        # shape produced by extract_links_from_search_results
        links = [
            {"title": r["title"], "url": r["url"], "index": r["index"]}
            for r in results
        ]
        md = format_links_to_markdown(links, numbered=True)
        leaders = re.findall(r"(?m)^\[([\d,\s-]+)\]", md)
        assert leaders == ["1", "2", "3"], f"got {leaders}"
        assert "," not in "".join(leaders)

    def test_default_mode_unchanged(self):
        """No numbered=True → legacy behaviour (comma-group unions)
        for the other 5 call sites."""
        links = [
            {"title": "A", "url": "http://a.onion/x", "index": "1"},
            {
                "title": "A",
                "url": "http://a.onion/x/",
                "index": "1224",
            },
        ]
        md = format_links_to_markdown(links)
        assert "[1, 1224]" in md  # legacy comma-group preserved


class TestSanitizerEscapedForm:
    def test_escaped_hyperlink_counted_as_used(self):
        md = (
            "Body cites [\\[73\\]](http://x.onion/a) and [\\[2\\]](http://y.onion/b).\n\n"
            "## 参考文献\n\n[73] Title\n   URL: http://x.onion/a\n"
        )
        start = md.find("## 参考文献")
        used = _used_nums_in_body(md, start)
        assert "73" in used, f"escaped form unseen: {used}"

    def test_plain_form_still_detected(self):
        md = "Uses [5] here.\n\n## 参考文献\n\n[5] T\n   URL: http://z.onion/c\n"
        start = md.find("## 参考文献")
        assert "5" in _used_nums_in_body(md, start)


class TestNumberedModeKeepsAssignIndices:
    def test_numbered_mode_uses_assign_index_not_position(self):
        """d24a84c9 regression: numbered=True renumbered rows by POSITION
        (enumerate) after extract_links dropped title-less entries, so
        block row numbers diverged from the assign_citation_numbers
        indices the LLM saw in its prompt — 20 citations died in the
        sanitize→enforce pincer. numbered=True must emit link['index']
        verbatim."""
        links = [
            {"title": "Bendibao", "url": "http://bendibao.com/waitan", "index": "3"},
            {"title": "Fourth", "url": "http://d.com/", "index": "4"},
        ]
        md = format_links_to_markdown(links, numbered=True)
        assert "[3] Bendibao (source nr: 3)" in md
        assert "[4] Fourth (source nr: 4)" in md
        assert "[1]" not in md
