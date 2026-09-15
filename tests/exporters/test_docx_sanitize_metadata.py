"""Tests for DOCXExporter (formerly ODTExporter).

Covers the security-critical _sanitize_metadata helper plus the new
DOCX-specific surface: footer XML construction for the
"第X页/共X页" page-number footer, title prepending via pandoc metadata,
and the LDR-attribution footer removal that mirrors the PDF path.
"""

import pytest

from local_deep_research.exporters.docx_exporter import DOCXExporter


@pytest.fixture
def exporter():
    """Create a DOCXExporter instance."""
    return DOCXExporter()


class TestSanitizeMetadataInjectionPrevention:
    """Tests for _sanitize_metadata argument injection prevention."""

    def test_normal_text_unchanged(self, exporter):
        assert (
            exporter._sanitize_metadata("My Research Report")
            == "My Research Report"
        )

    def test_double_dash_removed(self, exporter):
        assert (
            exporter._sanitize_metadata("--output=evil.sh") == "output=evil.sh"
        )

    def test_newlines_replaced_with_spaces(self, exporter):
        assert exporter._sanitize_metadata("line1\nline2") == "line1 line2"

    def test_multiple_double_dashes(self, exporter):
        result = exporter._sanitize_metadata("--flag1 --flag2 --flag3")
        assert "--" not in result

    def test_empty_string(self, exporter):
        assert exporter._sanitize_metadata("") == ""

    def test_only_dashes(self, exporter):
        assert exporter._sanitize_metadata("----") == ""

    def test_single_dash_preserved(self, exporter):
        assert exporter._sanitize_metadata("well-known") == "well-known"

    def test_unicode_preserved(self, exporter):
        assert exporter._sanitize_metadata("Ünïcödé Títlé") == "Ünïcödé Títlé"

    def test_triple_dash_removes_double_part(self, exporter):
        result = exporter._sanitize_metadata("---metadata")
        assert "--" not in result

    def test_mixed_injection_patterns(self, exporter):
        result = exporter._sanitize_metadata(
            "Title\n--variable=x\n--output=/tmp/evil"
        )
        assert "\n" not in result
        assert "--" not in result

    def test_embedded_single_dashes_preserved(self, exporter):
        assert (
            exporter._sanitize_metadata("state-of-the-art")
            == "state-of-the-art"
        )

    def test_tab_characters_preserved(self, exporter):
        result = exporter._sanitize_metadata("col1\tcol2")
        assert "\t" in result

    def test_extract_media_injection(self, exporter):
        result = exporter._sanitize_metadata("--extract-media=/tmp")
        assert "--" not in result

    def test_multiple_newlines(self, exporter):
        result = exporter._sanitize_metadata("a\nb\nc\nd")
        assert "\n" not in result
        assert "a b c d" == result


class TestDOCXExporterFormatMetadata:
    """The exporter now produces real DOCX, not ODT."""

    def test_format_name_is_docx(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        assert DOCXExporter().format_name == "docx"

    def test_file_extension_is_docx(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        assert DOCXExporter().file_extension == ".docx"

    def test_mimetype_is_office_openxml(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        assert (
            DOCXExporter().mimetype
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


class TestDOCXPageNumberFooterXML:
    """Footer XML carries the "第X页/共X页" pattern via OOXML field codes."""

    def test_footer_xml_uses_ooxml_field_codes_for_page_numbers(self):
        """PAGE / NUMPAGES are real OOXML fields so Word updates them
        on open; literal text "1" / "1" wouldn't update."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        xml = DOCXExporter._build_footer_xml()
        # PAGE field
        assert 'w:instr="PAGE' in xml
        # NUMPAGES field
        assert 'w:instr="NUMPAGES' in xml
        # Static framing text
        assert "第" in xml
        assert "页/共" in xml
        assert "页" in xml

    def test_footer_xml_is_a_well_formed_paragraph(self):
        """The XML must be parseable so DOCX writers don't reject it
        when injecting into word/footer1.xml."""
        from xml.etree import ElementTree

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        xml = DOCXExporter._build_footer_xml()
        # Must parse without error. We wrap the fragment so ET can
        # parse it as a complete document.
        wrapped = (
            '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            + xml + "</w:ftr>"
        )
        root = ElementTree.fromstring(wrapped)
        assert root.tag.endswith("}ftr")

    def test_footer_xml_centers_text(self):
        """Footer text is centered — matches the PDF page-number look."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        xml = DOCXExporter._build_footer_xml()
        assert "w:jc" in xml
        assert 'w:val="center"' in xml


class TestDOCXTitlePrepending:
    """The report title "关于{query}的研究报告" is prepended so it
    lands in the body before any TOC / chapter heading."""

    def test_title_is_prepended_when_query_provided(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        out = DOCXExporter._prepend_title(
            markdown_content="# 目录\n\n正文",
            query="量子计算简介",
        )
        # Title appears before the existing H1.
        assert out.startswith("# 关于量子计算简介的研究报告")
        assert "目录" in out
        # Original content is preserved.
        assert "正文" in out

    def test_no_title_when_query_missing(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        original = "# 目录\n\n正文"
        for q in (None, ""):
            assert DOCXExporter._prepend_title(original, q) == original


class TestDOCXFooterRemoved:
    """The "Generated by LDR..." body footer must not be appended —
    the user wants the report body clean (PDF path already does this)."""

    def test_no_ldr_footer_in_exported_markdown(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        out = DOCXExporter._scrub_body(
            "# 目录\n\n这是报告内容\n\nGenerated by [LDR - Local Deep Research] | Open Source AI Research Assistant"
        )
        assert "Generated by" not in out
        assert "Local Deep Research" not in out
        assert "这是报告内容" in out
