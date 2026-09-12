"""Tests for the 2026-08-30 doubled-hash heading fix (research 03b9f926).

The synthesis LLM (qwen3.5-opus:9b) emitted ``### ### 业务领域`` after
seeing prior subsection headings verbatim-with-hashes in the CONTENT
ALREADY WRITTEN context. Markdown parses that as an H3 whose visible
text literally contains "### 业务领域".

- Fix A (output-side): ``_strip_process_leakage`` collapses repeated
  ``#``-run prefixes on ATX heading lines.
- Fix B (cause-side): ``_summarize_finding_for_context`` emits heading
  text WITHOUT the ``#`` marks, removing the copy bait.
"""

import pytest

from local_deep_research.report_generator import IntegratedReportGenerator


@pytest.fixture
def generator():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    gen.max_context_sections = 3
    gen.max_context_chars = 4000
    return gen


class TestStripProcessLeakageCollapsesDoubledHeadings:
    def test_doubled_hash_h3_collapsed(self, generator):
        content = "### ### 业务领域\n\nWinrock 在农业领域开展工作 [[1]]。"
        result = generator._strip_process_leakage(content)
        assert result.startswith("### 业务领域\n")

    def test_triple_prefix_collapsed_to_single_run(self, generator):
        result = generator._strip_process_leakage(
            "#### ## ### 一、农业领域\n\n正文内容。"
        )
        assert result.startswith("#### 一、农业领域\n")

    def test_normal_headings_untouched(self, generator):
        content = (
            "### 业务领域\n\nWinrock 在农业领域开展工作 [[1]]。\n\n"
            "#### 一、农业领域\n\n正文。"
        )
        assert generator._strip_process_leakage(content) == content

    def test_hash_inside_body_text_untouched(self, generator):
        # A '#' mid-line (not an ATX heading) must not be rewritten.
        content = "使用 C# 与 F# 编写的工具。"
        assert generator._strip_process_leakage(content) == content


class TestSummarizeFindingStripsHeadingHashes:
    def test_context_headings_have_no_hash_marks(self, generator):
        finding = (
            "[组织概况与使命 > 业务领域]\n"
            "### 业务领域\n\n"
            "#### 一、农业领域\n\n"
            "Winrock 的农业项目覆盖全球。 [[1]]\n"
        )
        summary = generator._summarize_finding_for_context(finding)
        assert "Headings covered: 业务领域; 一、农业领域" in summary
        assert "###" not in summary

    def test_heading_only_line_falls_back_to_raw(self, generator):
        # A line of bare hashes ("#") must not vanish into an empty entry.
        finding = "[S > Sub]\n#\n\n正文摘录第一行。"
        summary = generator._summarize_finding_for_context(finding)
        assert "#" in summary  # degenerate line kept verbatim, not dropped
