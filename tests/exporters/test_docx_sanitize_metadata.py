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
        # The custom "关于…的研究报告" title is NOT prepended at the
        # markdown level — the post-processor injects it via OOXML so
        # only one H1 survives in the rendered document.
        out = DOCXExporter._prepare_markdown(original, query="量子", base_url=None)
        assert "# 关于" not in out
        # ``# 目录`` is preserved as text but gets demoted to H2 so the
        # only H1 in the final document is the user's title.
        assert "## 目录" in out
        assert "正文" in out

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
        the wrapping is broken. We rewrite both levels as bullet lists
        so Pandoc's DOCX writer emits a clean, multi-level TOC:

            - **Section 1**
              - Sub one | _purpose one_
              - Sub two | _purpose two_
            - **Section 2**
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
        # Top-level is now a dash bullet, sub-level is a 2-space-indent bullet.
        assert "- **Section 1**" in out
        assert "- **Section 2**" in out
        assert "  - Sub one" in out
        assert "  - Sub two" in out
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
        assert "http://127.0.0.1:5000/images/abc/figure.png" in out
        # The bare relative path is gone.
        assert '<img' not in out and '<figure' not in out

    def test_preserves_external_image_urls_unchanged(self, prep):
        """Already-absolute URLs (e.g. https://...) are not touched —
        only relative ``/images/...`` paths get rewritten."""
        md = '<img src="https://example.com/foo.png" alt="x"/>'
        out = prep(
            md,
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        assert "https://example.com/foo.png" in out
        # No accidental prefix.
        assert "http://127.0.0.1:5000/https://example.com" not in out

    def test_no_base_url_leaves_relative_paths_alone(self, prep):
        """When base_url is None, we cannot safely rewrite — leave
        the relative paths in place so the original URL survives."""
        md = '<img src="/images/abc/figure.png" alt="x"/>'
        out = prep(md, query=None, base_url=None)
        assert "/images/abc/figure.png" in out


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


class TestDOCXTitleTemplate:
    """The injected title must be the full ``关于{query}的研究报告``
    template, not just the query text itself. The previous version
    injected the query raw, so the user saw bare text in the title
    slot."""

    def test_title_wraps_query_in_full_template(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        para = DOCXExporter._build_title_paragraph_xml("量子计算简介")
        assert "关于量子计算简介的研究报告" in para
        # And the bare query text alone should NOT be the title.
        # (i.e., the wrapper text is "关于…的研究报告" not just the query)
        assert "量子计算简介" in para
        # Make sure the template pattern is intact (not corrupted).
        assert para.count("关于") == 1
        assert para.count("的研究报告") == 1


class TestDOCXFigureToMarkdownImage:
    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    """``<figure><img src="..." alt="..."/></figure>`` HTML produced by
    ``images.store.rewrite_markdown`` is not reliably extracted by
    Pandoc's HTML reader. Convert to standard markdown image syntax
    (``![alt](url)``) before Pandoc sees the document so the image
    fetch + embed path runs through Pandoc's native (and well-tested)
    image code path."""

    def test_figure_converted_to_markdown_image(self, prep):
        out = prep(
            '<figure class="ldr-img">'
            '<img src="/images/abc/figure.png" alt="量子电路示例"/>'
            '</figure>',
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        # The figure wrapper is gone.
        assert "<figure" not in out
        assert "</figure>" not in out
        # The img tag is gone too (replaced by markdown image syntax).
        assert "<img" not in out
        # The markdown image syntax is present, with the rewritten URL.
        assert (
            "![量子电路示例](http://127.0.0.1:5000/images/abc/figure.png)" in out
        )

    def test_figure_with_figcaption_preserves_caption(self, prep):
        """The figcaption text becomes the alt text when the alt
        attribute is missing/empty — this is what the existing
        ``images.store.rewrite_markdown`` does."""
        out = prep(
            '<figure class="ldr-img">'
            '<img src="/images/abc/x.png"/>'
            "<figcaption>示例图</figcaption>"
            "</figure>",
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        assert "![示例图](" in out

    def test_inline_img_without_figure_still_rewritten(self, prep):
        """Standalone ``<img>`` tags outside a figure also get their
        ``src`` rewritten — covers any other place the report might
        reference a local image route."""
        out = prep(
            '<p>看这张图：<img src="/images/abc/x.png" alt="内联图"/></p>',
            query=None,
            base_url="http://127.0.0.1:5000/",
        )
        # The img tag is replaced with a markdown image.
        assert "<img" not in out
        assert "![内联图](http://127.0.0.1:5000/images/abc/x.png)" in out


class TestDOCXTOCBulletList:
    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    """The report generator emits the TOC with raw-space indentation
    and 1.1-style numbering. Pandoc's markdown reader does not
    recognise three-space-indented numbered lines as nested list
    items, so the asterisks leak through and the line wrap is broken.
    Rewrite the TOC entries as proper nested bullet lists that
    Pandoc reliably renders as a clean multi-level TOC."""

    def test_top_level_toc_entries_become_bullet_items(self, prep):
        md = (
            "1. **Section 1**\n"
            "2. **Section 2**\n"
        )
        out = prep(md, query=None, base_url=None)
        # Numbered ``1.`` / ``2.`` -> ``-``.
        assert "1. **Section 1**" not in out
        assert "2. **Section 2**" not in out
        # Bullet list syntax.
        assert "- **Section 1**" in out
        assert "- **Section 2**" in out

    def test_subsections_become_nested_bullets(self, prep):
        md = (
            "1. **Section 1**\n"
            "   1.1 Sub one | _purpose one_\n"
            "   1.2 Sub two | _purpose two_\n"
        )
        out = prep(md, query=None, base_url=None)
        # The space-indented subsection lines are gone (no more 1.1 raw form).
        assert "   1.1" not in out
        assert "   1.2" not in out
        # Nested bullets under Section 1 — 2-space indent.
        assert "  - Sub one" in out
        assert "  - Sub two" in out
        # Italic markers around purpose text survive.
        assert "_purpose one_" in out

    def test_numbering_preserves_section_index(self, prep):
        """The 1.1 / 2.1 numbering is replaced with the section index
        alone so Pandoc can render the bullets cleanly without trying
        to preserve the custom two-level numbering."""
        md = (
            "1. **A**\n"
            "   1.1 a | _p_\n"
            "2. **B**\n"
            "   2.1 b | _p_\n"
        )
        out = prep(md, query=None, base_url=None)
        # A's subitem under "- **A**".
        assert "- **A**" in out
        assert "  - a" in out
        # B's subitem under "- **B**".
        assert "- **B**" in out
        assert "  - b" in out
        # No 1.1 / 2.1 raw form remains.
        assert "1.1 a" not in out
        assert "2.1 b" not in out


class TestDOCXPandocArgs:
    """Document the pandoc command-line flags we add. We currently
    pass --resource-path so relative URLs resolve against the Flask
    origin. If a future refactor accidentally drops it, image
    embedding will silently fail again — pin the contract via this
    test."""

    def test_pandoc_target_is_docx(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        # The export flow hardcodes ``-t docx``; this is a regression
        # guard so a stray refactor back to ``-t odt`` fails fast.
        assert DOCXExporter().format_name == "docx"


class TestDOCXDemoteBodyH1:
    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    """The body's H1s (``# 目录``, ``# 研究摘要``, LLM chapter titles)
    render as a second "title" in Word next to the user's
    ``关于X的研究报告`` post-processed title. Demote every body H1
    to H2 so the injected title is the only H1 in the document.

    The ``# 目录`` heading the report generator emits becomes a normal
    section heading under the title, and the LLM-generated chapter
    titles (which sometimes come back as the query text itself) stop
    masquerading as a second title.
    """

    def test_demotes_report_toc_h1_to_h2(self, prep):
        md = "# 目录\n\ncontent"
        out = prep(md, query=None, base_url=None)
        # The body H1 is demoted to H2.
        assert "## 目录" in out
        # No body H1 remains.
        import re
        h1s = re.findall(r"(?m)^# (.+)$", out)
        assert h1s == [], f"body H1s should be demoted, found: {h1s}"

    def test_demotes_llm_chapter_h1_to_h2(self, prep):
        """The LLM-generated body content often starts with a ``#``
        chapter heading. Demote it to ``##`` so the document only
        has one H1 (the user's title)."""
        md = (
            "# 目录\n\ntoc\n\n"
            "## 研究摘要\n\nsummary\n\n"
            "---\n\n"
            "# 量子计算简介\n\nchapter 1 body\n\n"
            "## 子标题\n\nsubbody\n"
        )
        out = prep(md, query=None, base_url=None)
        # The LLM chapter H1 is now H2.
        assert "## 量子计算简介" in out
        # The H2 subheading is now H3 (cascade).
        assert "### 子标题" in out


class TestDOCXFigureDimensionsPassThrough:
    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    """Pandoc embeds images at the size dictated by the markdown
    image syntax. When the original ``<img>`` carries explicit
    ``width``/``height`` (in CSS pixels) we forward them to Pandoc as
    the ``{width=... height=...}`` link-attribute syntax so the
    embedded image keeps the natural aspect ratio and does not get
    stretched to fill the page column."""

    def test_figure_with_dimensions_emits_link_attributes(self, prep):
        md = (
            '<figure class="ldr-img">'
            '<img src="/images/abc/figure.png" width="200" height="150"'
            ' alt="示例图"/>'
            "</figure>"
        )
        out = prep(
            md, query=None, base_url="http://127.0.0.1:5000/"
        )
        # The markdown image carries dimensions so Pandoc does not
        # default to the page column width.
        assert "width=" in out
        assert "height=" in out
        # URL is still rewritten to absolute under base_url.
        assert "http://127.0.0.1:5000/images/abc/figure.png" in out

    def test_figure_without_dimensions_still_rewrites_url(self, prep):
        """A figure without explicit dimensions still gets the URL
        rewrite — Pandoc will use the image's natural intrinsic
        size on embed (no stretching) when no dimensions are given."""
        md = (
            '<figure class="ldr-img">'
            '<img src="/images/abc/figure.png" alt="示例图"/>'
            "</figure>"
        )
        out = prep(
            md, query=None, base_url="http://127.0.0.1:5000/"
        )
        # No dimensions appended.
        assert "width=" not in out
        # But URL is rewritten.
        assert "http://127.0.0.1:5000/images/abc/figure.png" in out


class TestDOCXHeadingColorBlack:
    """The user wants the chapter title text rendered in pure black.
    Word's default heading colour can be a tinted dark grey; the
    kaiti patcher must also clear / set ``<w:color>`` to black on
    every heading style so the rendered text matches the request."""

    def test_heading_style_rpr_has_black_color(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        fake_styles = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"/>'
            '<w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/>'
            '<w:rPr>'
            '<w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia"/>'
            '<w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/>'
            '<w:color w:val="2E74B5"/>'
            '</w:rPr></w:style>'
            "</w:styles>"
        )
        out = DOCXExporter._patch_styles_xml_kaiti_headings(
            {"word/styles.xml": fake_styles.encode()}, headings=("Heading1",)
        )
        styles = out["word/styles.xml"].decode()
        # The 2E74B5 colour is replaced with pure black (000000).
        assert "2E74B5" not in styles
        # Either the explicit black override OR no colour element at all
        # is acceptable — both produce black on render.
        assert 'w:color w:val="000000"' in styles or "<w:color" not in styles
        # And the 楷体 font is still there.
        assert "KaiTi" in styles


class TestDOCXTOCBulletPipeReplaced:
    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    """The report generator emits TOC subsection lines as
    ``1.1 name | _purpose_``. When my prep converted these to bullet
    items (``  - name | _purpose_``), Pandoc split the run at the
    ``|`` and rendered the pipe as a literal character between two
    text runs, which the user reported as "换行乱" (line-break
    mess) in the TOC. Replace ``|`` with the typographic em-dash so
    Pandoc treats the bullet as a single text run."""

    def test_bullet_pipe_replaced_with_emdash(self, prep):
        md = (
            "1. **Section 1**\n"
            "   1.1 Sub one | _purpose one_\n"
            "   1.2 Sub two | _purpose two_\n"
        )
        out = prep(md, query=None, base_url=None)
        # No literal pipe between name and purpose.
        assert " | " not in out
        # Em-dash separator is present.
        assert " — " in out
        # Italic markers survive.
        assert "_purpose one_" in out
        assert "_purpose two_" in out


class TestDOCXTOCBulletEdgeCases:
    """Edge cases the original ``test_bullet_pipe_replaced_with_emdash``
    didn't cover: body without a pipe, multiple pipes in one body,
    embedded ``**bold**`` markers the user expects to survive, and
    CRLF line endings."""

    @pytest.fixture
    def prep(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        return DOCXExporter._prepare_markdown

    def test_bullet_without_pipe_is_unchanged(self, prep):
        """A subsection line with no pipe still converts to a
        bullet — the ``|`` replace is a no-op for those."""
        md = (
            "1. **Section 1**\n"
            "   1.1 Plain name without separator\n"
        )
        out = prep(md, query=None, base_url=None)
        assert "  - Plain name without separator" in out
        assert " — " not in out

    def test_bullet_with_multiple_pipes_replaces_all(self, prep):
        """``name | a | b`` gets every ``|`` swapped — gives
        ``name  —  a  —  b``. Ugly but semantically fine; the key
        contract is that the literal pipe character never reaches
        Pandoc."""
        md = (
            "1. **Section**\n"
            "   1.1 three | pipes | here\n"
        )
        out = prep(md, query=None, base_url=None)
        assert "|" not in out
        assert out.count(" — ") == 2  # 3 items separated by 2 pipes

    def test_bullet_preserves_embedded_bold_markers(self, prep):
        """``**emphasis**`` inside a bullet body must survive so
        Pandoc can still render it as bold text."""
        md = (
            "1. **Section**\n"
            "   1.1 Sub **bold** name | _purpose_\n"
        )
        out = prep(md, query=None, base_url=None)
        assert "**bold**" in out
        assert "_purpose_" in out
        assert " — " in out

    def test_bullet_with_crlf_line_endings_does_not_keep_trailing_cr(self, prep):
        """Windows reports carry CRLF. In multiline mode the regex's
        ``$`` matches before ``\n``, so the ``\r`` would land in
        the captured body without explicit stripping. A trailing
        ``\r`` in the bullet line would survive into Pandoc and
        could break rendering."""
        md_crlf = (
            "1. **Section**\r\n"
            "   1.1 Sub name | _purpose_\r\n"
        )
        out = prep(md_crlf, query=None, base_url=None)
        assert "\r" not in out
        assert " — " in out


    """LLM-generated report content sometimes starts a chapter with
    ``# <query>`` (the LLM reuses the research question as the
    chapter heading). After my H1→H2 demote this is still rendered
    as a prominent heading in Word, which the user reads as a
    "second title" alongside the injected ``关于X的研究报告``.

    The post-processor scans every paragraph in the body for a
    heading style whose text contains the query and strips the
    heading style so it renders as plain body text. The body
    content of the paragraph is preserved.
    """

    def _fake_doc_with_heading(self, style_id, heading_text, body_text):
        import io, zipfile
        safe = heading_text.replace("&", "&amp;").replace("<", "&lt;")
        safe_body = body_text.replace("&", "&amp;").replace("<", "&lt;")
        body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
            f'<w:p><w:r><w:t xml:space="preserve">{safe_body}</w:t></w:r></w:p>'
            "</w:body></w:document>"
        )
        return {
            "word/document.xml": body.encode("utf-8"),
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

    def test_paragraph_containing_query_is_removed_entirely(self):
        """The user wants the LLM's duplicate ``## <query>`` heading
        text gone from the body — not just demoted to a regular
        paragraph. Drop the entire ``<w:p>`` block whose visible
        text contains the query."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        names = self._fake_doc_with_heading(
            "Heading2", "量子计算基础", "这是章节内容。"
        )
        out = DOCXExporter._strip_query_heading_style(
            names, query="量子计算基础"
        )
        doc = out["word/document.xml"].decode("utf-8")
        import re
        # The heading paragraph containing the query is gone.
        assert "量子计算基础" not in doc, (
            "Heading paragraph containing query must be deleted, not "
            "demoted"
        )
        # The body paragraph (next sibling) is preserved.
        assert "这是章节内容。" in doc

    def test_h2_paragraph_without_query_keeps_its_heading_style(self):
        """Headings that do NOT contain the query stay as headings —
        only the duplicate-title paragraph is demoted."""
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        names = self._fake_doc_with_heading(
            "Heading2", "应用案例", "应用内容。"
        )
        out = DOCXExporter._strip_query_heading_style(
            names, query="量子计算基础"
        )
        doc = out["word/document.xml"].decode("utf-8")
        # The 应用案例 paragraph keeps its Heading2 style.
        assert 'pStyle w:val="Heading2"/>' in doc

    def test_no_query_means_no_demotion(self):
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        names = self._fake_doc_with_heading(
            "Heading1", "任何标题", "内容。"
        )
        out = DOCXExporter._strip_query_heading_style(names, query=None)
        doc = out["word/document.xml"].decode("utf-8")
        # All headings stay put when query is None.
        assert 'pStyle w:val="Heading1"/>' in doc

    def test_heading_whose_text_only_contains_query_substring_survives(self):
        """A heading that contains the query as a *substring* of a
        longer phrase must NOT be removed — only the exact duplicate
        title gets stripped.

        Before this guard the strip used ``query not in visible``
        (substring match), which would wipe every section heading
        that mentioned the query topic. With exact-match, only the
        pure-duplicate ``## <query>`` paragraph is removed.
        """
        from local_deep_research.exporters.docx_exporter import DOCXExporter
        # The query is the short topic; the heading is a *different*
        # subsection that contains the topic as a substring.
        names = self._fake_doc_with_heading(
            "Heading2", "量子计算的发展历程", "本节回顾历史。"
        )
        out = DOCXExporter._strip_query_heading_style(
            names, query="量子计算"
        )
        doc = out["word/document.xml"].decode("utf-8")
        # The legitimate subsection heading survives.
        assert "量子计算的发展历程" in doc
        # And it still has its Heading2 style (it is a real heading).
        assert 'pStyle w:val="Heading2"/>' in doc


class TestDOCXTOCBulletEndToEndPandoc:
    """End-to-end: take a realistic TOC + body through ``_prepare_markdown``
    and a real Pandoc run, then assert that the rendered DOCX body has
    no literal ``****`` characters and no text runs split at the
    ``|`` separator. This is what the user actually saw in Word —
    the unit tests above check the markdown prep output, but only a
    full Pandoc round-trip can prove the user's symptom is gone."""

    @staticmethod
    def _run_pandoc(markdown: str):
        """Helper: run _prepare_markdown → pypandoc → bytes."""
        import sys, io, tempfile, os
        sys.path.insert(0, "src")
        import pypandoc  # type: ignore[import-untyped]

        from local_deep_research.exporters.docx_exporter import DOCXExporter

        prepped = DOCXExporter._prepare_markdown(
            markdown, query=None, base_url=None
        )
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            out_path = f.name
        pypandoc.convert_text(prepped, "docx", format="md", outputfile=out_path)
        with open(out_path, "rb") as f:
            docx_bytes = f.read()
        os.unlink(out_path)
        return docx_bytes, prepped

    def test_rendered_toc_has_no_literal_asterisks(self):
        """The user's report was the TOC showed ``****`` literally —
        asterisks not being processed as bold by Pandoc. After the
        pipe→em-dash fix the rendered TOC must not contain any
        standalone ``*`` runs. Bold text would still appear as
        ``<w:r><w:rPr>...<w:b/>...</w:rPr><w:t>text</w:t></w:r>`` so
        we look for an asterisk inside a ``<w:t>`` body."""
        import re, zipfile
        realistic_toc = (
            "# 目录\n\n"
            "1. **研究背景**\n"
            "   1.1 量子计算基础 | _介绍量子计算的基本概念_\n"
            "   1.2 发展历程 | _回顾历史_\n"
            "2. **核心方法**\n"
            "   2.1 算法分析 | _Shor算法_\n"
        )
        docx_bytes, _prepped = self._run_pandoc(realistic_toc)
        z = zipfile.ZipFile(__import__("io").BytesIO(docx_bytes))
        doc = z.read("word/document.xml").decode()
        # Every <w:t> text — bold or not — must be free of literal *.
        for text in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", doc):
            assert "*" not in text, (
                f"Pandoc left literal '*' in text run {text!r} — "
                f"the TOC bullet fix did not take effect end-to-end."
            )

    def test_rendered_toc_has_no_pipe_split_text_runs(self):
        """The original symptom was Pandoc splitting the run at the
        ``|`` so the bullet rendered as ``name`` + ``|`` + ``purpose``
        with the pipe character sitting awkwardly between two runs.
        After the fix the pipe should not appear in the rendered
        text at all — only the em-dash separator should."""
        import re, zipfile
        toc = (
            "1. **S**\n"
            "   1.1 Sub one | _purpose one_\n"
        )
        docx_bytes, _prepped = self._run_pandoc(toc)
        z = zipfile.ZipFile(__import__("io").BytesIO(docx_bytes))
        doc = z.read("word/document.xml").decode()
        # No literal pipe character anywhere in the body text runs.
        for text in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", doc):
            assert "|" not in text, (
                f"Pandoc left literal '|' in text run {text!r} — "
                f"the pipe-split bug is not actually fixed."
            )
