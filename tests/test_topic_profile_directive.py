"""Tests for the 2026-09-12 subject-classification directive.

The structure-determination prompt must instruct the LLM to classify
the research subject (person / organization / event / general) and, for
person/org/event subjects, to include every applicable mandatory key
item as a subsection of the report TOC.
"""

import pytest

from local_deep_research.report_generator import IntegratedReportGenerator

_MANDATORY_MARKERS = {
    "person": [
        "人物背景介绍",
        "人物联系方式",
        "直系亲属人员基本情况",
        "习惯爱好",
        "性格特征",
        "个人及家庭财产情况",
        "主要社会关系",
        "宗教信仰",
        "教育经历和工作履历",
        "政治主张",
        "社会活动情况",
        "与中国相关的言论和商业活动情况",
    ],
    "organization": [
        "组织背景介绍",
        "机构注册国家及办公地址",
        "联系方式",
        "互联网官方主页地址",
        "组织成立的宗旨和目标",
        "主要核心成员详情",
        "组织主要历史活动情况",
        "组织经费来源详情",
        "与其它组织关联情况",
        "在中国境内的组织架构及其活动情况",
    ],
    "event": [
        "事件最早发生时间和地点",
        "事件发生的背景和原因",
        "是否有政治因素影响",
        "事件发展主要过程和社会影响",
        "事件涉及相关方和主要人物详情",
        "主流媒体报道情况",
        "政府对该事件的反应和介入情况",
        "未来事件发展态势预测",
    ],
}


@pytest.fixture
def generator():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    return gen


def _capture_structure_prompt(gen, query):
    """Run _determine_report_structure with a stub model, return the prompt."""
    captured = {}

    class _StubModel:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return type(
                "R", (), {"content": "STRUCTURE\n1. 概述\n   - 背景 | 简介\nEND_STRUCTURE"}
            )()

    gen.model = _StubModel()
    gen._determine_report_structure(
        {"current_knowledge": "stub content"}, query
    )
    return captured["prompt"]


class TestTopicProfileDirective:
    def test_directive_contains_all_mandatory_items(self, generator):
        directive = generator._TOPIC_PROFILE_DIRECTIVE
        for markers in _MANDATORY_MARKERS.values():
            for marker in markers:
                assert marker in directive, marker

    def test_structure_prompt_embeds_directive(self, generator):
        prompt = _capture_structure_prompt(generator, "Winrock International")
        assert "SUBJECT CLASSIFICATION" in prompt
        assert "人物背景介绍" in prompt
        assert "组织经费来源详情" in prompt
        assert "未来事件发展态势预测" in prompt

    def test_directive_precedes_structure_instructions(self, generator):
        prompt = _capture_structure_prompt(generator, "某公司调研")
        assert prompt.index("SUBJECT CLASSIFICATION") < prompt.index(
            "Determine the most appropriate report structure"
        )
