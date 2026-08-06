"""Tests for report chapter-scaffolding heading localization.

`_format_final_report` previously emitted hardcoded English heading
strings (`# Table of Contents`, `# Research Summary`, `## Sources`),
ignoring ``report.language``. With the default ``zh-CN`` a Chinese
user got a Chinese body but English chapter scaffolding. These tests
assert the chapter scaffolding renders in the user's chosen language.

Note: the report summary block has historically been DISCARDED by the
per-section renumber rebuild in ``_format_final_report`` (see the
pre-existing ``report_parts[1:1]`` slice in that file). That
pre-existing rendering bug is out of scope here — we only assert what
the user actually sees in the report: the TOC heading and the
``## Sources`` heading.
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
        Chinese for zh-CN. The block is historically dropped by the
        rebuild (pre-existing bug, out of scope) — we only check the
        helper's return value here."""
        gen = _make_generator()
        assert gen._get_chapter_headings()["summary"] == "# 研究摘要"

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
