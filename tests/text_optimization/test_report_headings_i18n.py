"""Tests for report chapter-scaffolding heading localization.

`_format_final_report` previously emitted hardcoded English heading
strings (`# Table of Contents`, `# Research Summary`, `## Sources`),
ignoring ``report.language``. With the default ``zh-CN`` a Chinese
user got a Chinese body but English chapter scaffolding. These tests
assert the chapter scaffolding renders in the user's chosen language.

Note: the report summary block (heading + body lines) IS rendered into
the final report content by ``_format_final_report`` — the per-section
renumber rebuild preserves it via ``report_parts[1:6]``. Both the
heading and the two body lines are localized together via
``_get_chapter_headings`` so a zh-CN report shows Chinese summary text
throughout, not a Chinese heading over English body lines.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_generator(settings_snapshot=None):
    from local_deep_research.report_generator import IntegratedReportGenerator

    system = MagicMock()
    system.strategy = MagicMock()
    system.strategy.settings_snapshot = {"search.iterations": 1}
    system.strategy.max_iterations = 1
    system.all_links_of_system = []
    model = MagicMock()
    return IntegratedReportGenerator(
        llm=model, search_system=system, settings_snapshot=settings_snapshot
    )


def _basic_structure():
    return [
        {
            "name": "示例章节",
            "subsections": [
                {"name": "示例子章节", "purpose": "示例目的"},
            ],
        }
    ]


def _basic_sections():
    return {
        "示例章节": "# 1. 示例章节\n\n## 1.1 示例子章节\n\n示例正文内容。\n",
    }


class TestReportHeadingsLocalizedToZhCN:
    """Default ``report.language=zh-CN`` reports must use Chinese chapter
    scaffolding instead of hardcoded English.
    """

    def test_toc_heading_is_chinese(self):
        # No settings_snapshot → default report.language is zh-CN
        gen = _make_generator()
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "示例查询"
        )
        content = report["content"]
        assert "# 目录" in content
        assert "# Table of Contents" not in content

    def test_research_summary_constant_is_chinese(self):
        """The localized Summary heading constant must be available in
        Chinese for zh-CN."""
        gen = _make_generator()
        headings = gen._get_chapter_headings()
        assert headings["summary"] == "# 研究摘要"

    def test_research_summary_body_is_chinese(self):
        """The summary body lines (under the # 研究摘要 heading) must
        render in Chinese for zh-CN, not the historical hard-coded
        English. Regression for the zh-CN heading + English body split."""
        gen = _make_generator()
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "示例查询"
        )
        content = report["content"]
        assert "本报告使用高级搜索系统完成研究。" in content
        assert "研究过程中针对每个章节与子小节进行了定向检索。" in content
        assert "advanced search system" not in content
        assert "Research included targeted searches" not in content

    def test_sources_heading_is_chinese(self):
        gen = _make_generator()
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "示例查询"
        )
        content = report["content"]
        assert "## 参考文献" in content
        assert "## Sources" not in content


class TestReportHeadingsEnglishWhenLanguageIsEn:
    """Explicit ``report.language=en`` reports must use English scaffolding.

    This guards against over-correction: the headings should localize
    to Chinese only when the user actually picked Chinese.
    """

    def test_toc_heading_is_english(self):
        gen = _make_generator(settings_snapshot={"report.language": "en"})
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "example"
        )
        content = report["content"]
        assert "# Table of Contents" in content
        assert "# 目录" not in content

    def test_sources_heading_is_english(self):
        gen = _make_generator(settings_snapshot={"report.language": "en"})
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "example"
        )
        content = report["content"]
        assert "## Sources" in content
        assert "## 参考文献" not in content

    def test_research_summary_body_is_english(self):
        """Non-zh-CN locales get the English summary body verbatim
        (fall-through contract preserved — no behavior change for
        English/unknown locales)."""
        gen = _make_generator(settings_snapshot={"report.language": "en"})
        report = gen._format_final_report(
            _basic_sections(), _basic_structure(), "example"
        )
        content = report["content"]
        assert "This report was researched using an advanced search system." in content
        assert "Research included targeted searches for each section and subsection." in content
        assert "本报告使用高级搜索系统完成研究。" not in content


# --- empty-subsection placeholder localization -------------------------------


def test_empty_subsection_placeholder_is_chinese_for_zh_cn():
    """A subsection whose research produced nothing must not emit the
    English "*Limited information was found for this subsection.*" into
    a Chinese report body.
    """
    gen = _make_generator({"report.language": {"value": "zh-CN"}})
    headings = gen._get_chapter_headings()

    placeholder = headings["empty_subsection"]
    assert "Limited information" not in placeholder
    # No Latin letters at all — the body language must stay consistent.
    assert not any("a" <= c.lower() <= "z" for c in placeholder)
    assert "信息" in placeholder


def test_empty_subsection_placeholder_is_english_for_en():
    gen = _make_generator({"report.language": {"value": "en"}})
    placeholder = gen._get_chapter_headings()["empty_subsection"]

    assert "Limited information" in placeholder


def test_empty_subsection_placeholder_falls_back_to_english():
    """Unknown locale falls through to the English set, like the other
    scaffolding keys — never a missing key.
    """
    gen = _make_generator({"report.language": {"value": "kl-KL"}})
    placeholder = gen._get_chapter_headings()["empty_subsection"]

    assert "Limited information" in placeholder


def test_empty_subsection_placeholder_present_without_snapshot():
    """Legacy callers build the generator via __new__ and skip __init__;
    the key must still resolve (zh-CN default), not KeyError.
    """
    from local_deep_research.report_generator import IntegratedReportGenerator

    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    headings = gen._get_chapter_headings()

    assert "empty_subsection" in headings
    assert "Limited information" not in headings["empty_subsection"]
