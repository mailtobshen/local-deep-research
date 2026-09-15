"""Tests for DOCXExporter (formerly ODTExporter).

Covers the security-critical _sanitize_metadata helper plus the new
DOCX-specific surface: footer XML construction for the
"第X页/共X页" page-number footer, title prepending via pandoc metadata,
and the LDR-attribution footer removal that mirrors the PDF path.
"""

import pytest
import re

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


class TestDOCXTitleInjection:
    """The report title "关于{query}的研究报告" is injected as a
    styled OOXML paragraph via the DOCX post-processor — not as a
    markdown heading.

    Markdown-level injection was rejected because the report
    generator already emits ``# 目录`` as the first heading; prepending
    another ``# 关于X的研究报告`` would produce two visible titles
    in Word. The post-processor therefore:

    1. Returns the body untouched from ``_prepare_markdown``.
    2. Injects one styled ``<w:p>`` at the very top of
       ``<w:body>`` carrying the title text.

    This test class covers the markdown-side contract: when ``query``
    is given, the title is NOT prepended into the markdown body, and
    a separate injection helper produces a properly-styled OOXML
    paragraph carrying the title text.
    """

    def test_markdown_body_unchanged_when_query_given(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        original = "# 目录\n\n正文"
        # No H1 is prepended at the markdown level.
        out = DOCXExporter._prepare_markdown(original, query="量子", base_url=None)
        assert out == original
        assert "# 关于" not in out
        # The existing ``# 目录`` heading is preserved verbatim.
        assert "目录" in out

    def test_inject_title_paragraph_has_correct_styling(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        para = DOCXExporter._build_title_paragraph_xml("量子计算简介")
        # Centered alignment.
        assert "w:jc" in para
        assert 'w:val="center"' in para
        # 22pt = 44 half-points (二号 = 22pt).
        assert 'w:val="44"' in para
        # SimHei / 黑体 family.
        assert "SimHei" in para
        assert "Heiti SC" in para
        # Bold weight.
        assert "w:b" in para
        # Title text present.
        assert "量子计算简介" in para
        # Wrapped in a w:p (paragraph) element so it sits at body top.
        assert para.startswith("<w:p>")
        assert para.endswith("</w:p>")

    def test_inject_title_handles_html_metachars_safely(self):
        """Query text is HTML-escaped before XML insertion — defence
        in depth against the query string itself being malformed."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        para = DOCXExporter._build_title_paragraph_xml("<script>x</script>")
        # No raw tag survives in the OOXML.
        assert "<script>" not in para
        assert "&lt;script&gt;" in para


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


class TestDOCXMarkdownPrep:
    """The DOCX exporter pre-processes the markdown before Pandoc:
    stripping standalone horizontal rules, fixing the TOC's broken
    list formatting (the report-generator emits space-indented
    sub-sections that Pandoc does not recognise as nested lists), and
    rewriting relative image URLs to absolute so Pandoc fetches them
    from the same Flask origin the rewritten routes serve."""

    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    def test_strips_standalone_horizontal_rules(self, prep):
        """Standalone ``---`` is removed everywhere, but table
        separators (``|---|---|``) survive — the user asked for no
        long horizontal lines in the report body, with tables exempted.
        """
        md = (
            "# Title\n\n"
            "para one\n\n"
            "---\n\n"
            "para two after rule\n\n"
            "| col1 | col2 |\n"
            "|---|---|\n"
            "| a | b |\n\n"
            "---\n\n"
            "para three\n"
        )
        out = prep(md, query=None, base_url=None)
        # The two standalone rules vanish.
        assert out.count("\n---\n") == 0
        # The table separator survives.
        assert "|---|---|" in out
        # The actual table content survives.
        assert "| a | b |" in out

    def test_reformats_space_indented_subsections_as_nested_list(self, prep):
        """The report generator emits subsection lines indented with
        raw spaces (``   1.1 name | _purpose_``) — Pandoc does NOT treat
        those as nested list items, so the asterisks leak through and
        the wrapping is broken. Reformat to a proper nested list so
        Pandoc's DOCX writer emits a clean, multi-level TOC.
        """
        md = (
            "1. **Section 1**\n"
            "   1.1 Sub one | _purpose one_\n"
            "   1.2 Sub two | _purpose two_\n"
            "2. **Section 2**\n"
        )
        out = prep(md, query=None, base_url=None)
        # No raw space-indented lines should survive.
        assert "   1.1" not in out
        assert "   1.2" not in out
        # The bold markers for top-level sections are preserved.
        assert "**Section 1**" in out
        assert "**Section 2**" in out
        # The subsection lines are now nested-list items (4-space
        # indent) so Pandoc's DOCX writer can render them properly.
        assert "    1." in out or "\n    2." in out
        # Italic markers around purpose text survive.
        assert "_purpose one_" in out
        assert "_purpose two_" in out

    def test_rewrites_relative_image_urls_to_absolute(self, prep):
        """``/images/<id>/<fn>`` from images.store.rewrite_markdown is
        rewritten to ``<base_url>images/<id>/<fn>`` so Pandoc can fetch
        the local Flask image route directly.
        """
        md = (
            '<figure class="ldr-img">'
            '<img src="/images/abc/figure.png" alt="示例图"/>'
            '</figure>'
        )
        out = prep(
            md,
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        # The rewritten URL points back at the same Flask origin.
        assert "src=\"http://127.0.0.1:5000/images/abc/figure.png\"" in out
        # The bare relative path is gone.
        assert 'src="/images/abc/figure.png"' not in out

    def test_preserves_external_image_urls_unchanged(self, prep):
        """Already-absolute URLs (e.g. https://...) are not touched —
        only relative ``/images/...`` paths get rewritten."""
        md = '<img src="https://example.com/foo.png" alt="x"/>'
        out = prep(
            md,
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        assert 'src="https://example.com/foo.png"' in out
        # No accidental prefix.
        assert "http://127.0.0.1:5000/https://example.com" not in out

    def test_no_base_url_leaves_relative_paths_alone(self, prep):
        """When base_url is None, we cannot safely rewrite — leave
        the relative paths in place so the original URL survives."""
        md = '<img src="/images/abc/figure.png" alt="x"/>'
        out = prep(md, query=None, base_url=None)
        assert 'src="/images/abc/figure.png"' in out


class TestDOCXFooterXMLNoColor:
    """Footer text must not carry a colour attribute — the user wants
    all document text in pure black."""

    def test_footer_xml_has_no_color_element(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        xml = DOCXExporter._build_footer_xml()
        # No gray or any colour override on the page-number run.
        assert "w:color" not in xml


class TestDOCXPostProcessIntegration:
    """End-to-end: feed a fake Pandoc-style DOCX zip through
    ``_inject_footer_into_docx`` and verify all the new pieces land:
    page-number footer, title paragraph, body-font default."""

    @staticmethod
    def _fake_pandoc_docx():
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>",
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>",
            )
            z.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body>"
                '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
                "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/></w:sectPr>"
                "</w:body></w:document>",
            )
            z.writestr(
                "word/styles.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\"/>"
                "</w:styles>",
            )
        return buf.getvalue()

    def test_title_injected_at_body_top_when_query_given(self):
        import io
        import zipfile

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        out_bytes = DOCXExporter._inject_footer_into_docx(
            self._fake_pandoc_docx(), query="量子计算简介"
        )
        z = zipfile.ZipFile(io.BytesIO(out_bytes))
        doc = z.read("word/document.xml").decode("utf-8")
        # The title paragraph appears before the body content
        # ("<w:p>...量子计算简介...</w:p>" comes before the
        # existing "<w:p>...正文...</w:p>").
        title_idx = doc.index("量子计算简介")
        body_idx = doc.index("正文")
        assert title_idx < body_idx, (
            "Title must be injected before the existing body content"
        )
        # Centred + 二号 + 黑体 family.
        assert 'w:val="center"' in doc
        assert 'w:val="44"' in doc  # 22pt = 44 half-points
        assert "SimHei" in doc
        # Bold weight.
        assert "<w:b/>" in doc
        # No colour override.
        assert "w:color" not in doc

    def test_no_title_when_query_missing(self):
        import io
        import zipfile

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        out_bytes = DOCXExporter._inject_footer_into_docx(
            self._fake_pandoc_docx(), query=None
        )
        z = zipfile.ZipFile(io.BytesIO(out_bytes))
        doc = z.read("word/document.xml").decode("utf-8")
        # The original body is untouched — no title paragraph added.
        assert "正文" in doc
        # The fake body had no "量子" anywhere; still none.
        assert "量子" not in doc

    def test_body_default_font_set_to_simsun_wuhao(self):
        import io
        import zipfile

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        out_bytes = DOCXExporter._inject_footer_into_docx(
            self._fake_pandoc_docx(), query="x"
        )
        z = zipfile.ZipFile(io.BytesIO(out_bytes))
        styles = z.read("word/styles.xml").decode("utf-8")
        # 宋体 family.
        assert "SimSun" in styles
        # 10.5pt = 21 half-points (五号).
        assert 'w:val="21"' in styles
        # rPrDefault is present so the default applies to unstyled
        # body text (which is most of the report).
        assert "<w:rPrDefault>" in styles
        # Normal style still exists (Pandoc emits it; we did not delete it).
        assert 'w:styleId="Normal"' in styles

    def test_post_process_is_idempotent(self):
        """Running the post-processor twice must not duplicate the
        title or footer."""
        import io
        import zipfile

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        once = DOCXExporter._inject_footer_into_docx(
            self._fake_pandoc_docx(), query="量子"
        )
        twice = DOCXExporter._inject_footer_into_docx(once, query="量子")
        z1 = zipfile.ZipFile(io.BytesIO(once))
        z2 = zipfile.ZipFile(io.BytesIO(twice))
        # Footer relationship added at most once.
        rels1 = z1.read("word/_rels/document.xml.rels").decode("utf-8")
        rels2 = z2.read("word/_rels/document.xml.rels").decode("utf-8")
        assert rels1.count("footer1.xml") == rels2.count("footer1.xml")
        # Title text appears at most once in document.xml.
        doc1 = z1.read("word/document.xml").decode("utf-8")
        doc2 = z2.read("word/document.xml").decode("utf-8")
        assert doc1.count("量子") == doc2.count("量子")


class TestDOCXHeadingFontKaiti:
    """User requested all section headings (h1/h2/h3/...) to be 楷体.

    The post-processor must patch every ``HeadingN`` style in
    ``word/styles.xml`` so Word/LibreOffice renders the report
    chapters in 楷体. Heading *sizes* are unchanged (the user only
    asked to swap the font, not resize the chapter titles).
    """

    def test_heading_styles_use_kaiti_after_postprocess(self):
        import io
        import zipfile

        from local_deep_research.exporters.docx_exporter import DOCXExporter
        # Build a fake styles.xml that mimics Pandoc's DOCX output:
        # a Normal style + Heading1/Heading2 with theme-based rFonts
        # + their own size values.
        fake_styles = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            "<w:name w:val=\"Normal\"/></w:style>"
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/>'
            "<w:rPr>"
            '<w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/>'
            '<w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/>'
            "</w:rPr></w:style>"
            '<w:style w:type="paragraph" w:styleId="Heading2">'
            '<w:name w:val="heading 2"/>'
            "<w:rPr>"
            '<w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/>'
            '<w:b/><w:bCs/><w:sz w:val="28"/><w:szCs w:val="28"/>'
            "</w:rPr></w:style>"
            '<w:style w:type="paragraph" w:styleId="Heading3">'
            '<w:name w:val="heading 3"/>'
            "<w:rPr>"
            '<w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/>'
            '<w:b/><w:bCs/><w:sz w:val="24"/><w:szCs w:val="24"/>'
            "</w:rPr></w:style>"
            "</w:styles>"
        )
        # Run the styles through our patcher.
        names = {
            "word/styles.xml": fake_styles.encode("utf-8"),
            # Required siblings of the post-process so the function runs end-to-end.
            "word/document.xml": (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p/></w:body></w:document>"
            ),
            "[Content_Types].xml": (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                b'<Default Extension="xml" ContentType="application/xml"/>'
                b'<Override PartName="/word/document.xml" '
                b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                b"</Types>"
            ),
        }
        out = DOCXExporter._inject_footer_into_docx(
            __import__("io").BytesIO().getvalue(),  # placeholder; will not be used
            query=None,
        ) if False else DOCXExporter._patch_styles_xml_kaiti_headings(
            names, headings=("Heading1", "Heading2", "Heading3")
        )
        styles = out["word/styles.xml"].decode("utf-8")

        # 1) Every heading style gets the 楷体 family on its rFonts.
        for h in ("Heading1", "Heading2", "Heading3"):
            m = re.search(
                rf'<w:style[^>]*w:styleId="{h}".*?</w:style>',
                styles,
                re.DOTALL,
            )
            assert m, f"{h} style not found"
            block = m.group(0)
            assert "KaiTi" in block or "SimKai" in block, (
                f"{h} rPr must reference 楷体 family"
            )

        # 2) Heading sizes are NOT touched.
        assert 'w:val="32"' in styles  # h1 stays 16pt
        assert 'w:val="28"' in styles  # h2 stays 14pt
        assert 'w:val="24"' in styles  # h3 stays 12pt

    def test_kaiti_helper_targets_only_heading_styles(self):
        """Non-heading styles (Normal, Quote, etc.) must not get
        楷体 — only Heading1..N do."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        fake = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            "<w:rPr><w:rFonts w:asciiTheme=\"minorHAnsi\"/></w:rPr></w:style>"
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            "<w:rPr><w:rFonts w:asciiTheme=\"majorHAnsi\"/></w:rPr></w:style>"
            "</w:styles>"
        )
        names = {"word/styles.xml": fake.encode("utf-8")}
        out = DOCXExporter._patch_styles_xml_kaiti_headings(
            names, headings=("Heading1",)
        )
        styles = out["word/styles.xml"].decode("utf-8")
        # Normal is untouched.
        normal_block = re.search(
            r'<w:style[^>]*w:styleId="Normal".*?</w:style>',
            styles,
            re.DOTALL,
        ).group(0)
        assert "KaiTi" not in normal_block and "SimKai" not in normal_block
        # Heading1 is patched.
        h1_block = re.search(
            r'<w:style[^>]*w:styleId="Heading1".*?</w:style>',
            styles,
            re.DOTALL,
        ).group(0)
        assert "KaiTi" in h1_block or "SimKai" in h1_block
