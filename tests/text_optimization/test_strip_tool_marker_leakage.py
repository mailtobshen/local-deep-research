"""Strip ``[[tool_name]]`` leakage from synthesis bodies.

Observed 2026-08-25 (research 9fd73401, 17:45): the LLM decorated
section headings with the literal tool marker ``[[research_subtopic]]``
— e.g. ``DJI O3 Air Unit（O3 Enterprise）[[research_subtopic]]`` —
in at least 3+ headings of the final report. The prompt teaches the
``[[N]](url)`` citation shape and names the ``research_subtopic``
tool; the model conflated the two. No cleanup stage recognised the
token, so it shipped verbatim.

Fix: ``strip_tool_marker_leakage`` removes ``[[word]]`` tokens whose
inner word is a known tool name (research_subtopic today; the list is
extensible) from the body — anywhere (headings or prose). Numeric
``[[73]]`` citation tokens are NOT touched.
"""

import pytest

from local_deep_research.advanced_search_system.strategies.langgraph_agent_strategy import (
    strip_tool_marker_leakage,
)


class TestStripToolMarkers:
    def test_heading_marker_removed(self):
        body = "## DJI O3 Air Unit（O3 Enterprise）[[research_subtopic]]\n\n内容段落。"
        out = strip_tool_marker_leakage(body)
        assert "research_subtopic" not in out
        assert "DJI O3 Air Unit" in out

    def test_prose_marker_removed(self):
        body = "Some sentence [[research_subtopic]] continues here."
        out = strip_tool_marker_leakage(body)
        assert "research_subtopic" not in out
        assert "Some sentence continues here." == out

    def test_numeric_double_bracket_untouched(self):
        """``[[73]]`` is a (bare) citation token — must survive."""
        body = "Claim [[73]] about drones."
        out = strip_tool_marker_leakage(body)
        assert "[[73]]" in out

    def test_hyperlink_citation_untouched(self):
        body = "Claim [[7]](http://x.onion/a) stays."
        out = strip_tool_marker_leakage(body)
        assert "[[7]](http://x.onion/a)" in out

    def test_trailing_whitespace_cleaned_in_headings(self):
        body = "## 4.1 电压等级 [[research_subtopic]]\n\n正文"
        out = strip_tool_marker_leakage(body)
        assert out.splitlines()[0] == "## 4.1 电压等级"

    def test_clean_body_unchanged(self):
        body = "## 正常标题\n\n正文 [[1]] 引用。"
        assert strip_tool_marker_leakage(body) == body
