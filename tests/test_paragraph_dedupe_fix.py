"""Tests for the 2026-09-13 paragraph-level dedup (research c34cb8fa).

The block-level SEC-DEDUP missed a paragraph copied verbatim INSIDE one
subsection (7.2 资助方与合作伙伴 duplicated the 资金来源 paragraph with
identical [19]/[24]/[67] markers). Paragraph dedup runs at the same
choke point with the same normalization contract.
"""

import re

import pytest

from local_deep_research.report_generator import IntegratedReportGenerator

# The actual duplicated paragraph from research c34cb8fa, abridged.
_P1 = (
    "威诺克国际的资金来源呈现出高度多元化的特征，主要依赖于政府合同、影响力投资、"
    "企业伙伴关系以及专业服务收入。其中，美国国际开发署（USAID）是其最重要的单一"
    "政府资助方，该组织承接了多个长期项目合同，涵盖农业推广、气候行动及反人口贩卖"
    "等领域 [[19]]。例如，在孟加拉国开展的 ClimAct 活动以及在尼泊尔实施的反人口贩卖 "
    "Hamro Samman 项目，均通过 USAID 的资助渠道获得资金支持 [[24]]。"
)
_P2 = "威诺克国际还积极与美国农业部合作，开展了多个农业援助项目。"


@pytest.fixture
def generator():
    return IntegratedReportGenerator.__new__(IntegratedReportGenerator)


class TestParagraphDedup:
    def test_verbatim_paragraph_dup_dropped(self, generator):
        content = f"### 7.2 资助方与合作伙伴\n\n{_P1}\n\n{_P1}\n\n{_P2}"
        result = generator._dedupe_repeated_subsections(content)
        assert result.count("威诺克国际的资金来源呈现出高度多元化") == 1
        assert _P2 in result

    def test_dup_across_subsections_dropped(self, generator):
        content = (
            f"### 7.1 组织治理\n\n{_P1}\n\n### 7.2 资助方与合作伙伴\n\n{_P1}\n\n"
        )
        result = generator._dedupe_repeated_subsections(content)
        assert result.count("威诺克国际的资金来源呈现出高度多元化") == 1

    def test_renumbered_citations_still_dedup(self, generator):
        """LLM often renumbers citations between copies."""
        p1_alt = re.sub(r"\[\[19\]\]", "[[54]]", _P1)
        content = f"### 7.2 资助方与合作伙伴\n\n{_P1}\n\n{p1_alt}\n\n"
        result = generator._dedupe_repeated_subsections(content)
        assert result.count("威诺克国际的资金来源呈现出高度多元化") == 1

    def test_single_subsection_still_gets_paragraph_dedup(self, generator):
        """Pre-fix gap: <2 blocks skipped dedup entirely."""
        content = f"### 7.2 资助方与合作伙伴\n\n{_P1}\n\n{_P1}\n\n"
        result = generator._dedupe_repeated_subsections(content)
        assert result.count("威诺克国际的资金来源呈现出高度多元化") == 1

    def test_distinct_paragraphs_untouched(self, generator):
        content = f"### 7.2 资助方与合作伙伴\n\n{_P1}\n\n{_P2}\n\n"
        assert generator._dedupe_repeated_subsections(content) == content

    def test_short_repeated_lines_not_dropped(self, generator):
        # Below the 40-normalized-char floor: boilerplate-ish short lines
        # repeat legitimately and must survive.
        content = "### 概述\n\n详见下文。\n\n详见下文。\n\n正文开始。"
        assert generator._dedupe_repeated_subsections(content) == content

    def test_heading_lines_excluded_from_key(self, generator):
        # Same paragraph under two different headings: paragraph itself
        # is the duplicate; headings must not block detection. (When the
        # two whole blocks normalize identically, the pre-existing BLOCK
        # dedup legitimately drops the second block with its heading.)
        content = f"### 一、资金\n\n{_P1}\n\n### 二、资助\n\n{_P1}\n\n"
        result = generator._dedupe_repeated_subsections(content)
        assert result.count("威诺克国际的资金来源呈现出高度多元化") == 1
        assert "### 一、资金" in result
