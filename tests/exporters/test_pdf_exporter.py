"""Tests for PDFExporter (refactored version)."""

import pytest
from unittest.mock import patch, MagicMock

from local_deep_research.web.services.pdf_service import WEASYPRINT_AVAILABLE

# Skip all tests that need real PDF generation when WeasyPrint is unavailable
needs_weasyprint = pytest.mark.skipif(
    not WEASYPRINT_AVAILABLE,
    reason="WeasyPrint system libraries not available",
)


class TestPDFExporterProperties:
    """Tests for PDFExporter properties."""

    def test_format_name_is_pdf(self):
        """Test that format_name is 'pdf'."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        exporter = PDFExporter()

        assert exporter.format_name == "pdf"

    def test_file_extension_is_pdf(self):
        """Test that file_extension is '.pdf'."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        exporter = PDFExporter()

        assert exporter.file_extension == ".pdf"

    def test_mimetype_is_correct(self):
        """Test that mimetype is correct for PDF."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        exporter = PDFExporter()

        assert exporter.mimetype == "application/pdf"


class TestPDFExporterExport:
    """Tests for PDFExporter.export method."""

    @pytest.fixture
    def exporter(self):
        """Create PDFExporter instance."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        return PDFExporter()

    @needs_weasyprint
    def test_returns_export_result(self, exporter, simple_markdown):
        """Test that export returns ExportResult."""
        from local_deep_research.exporters import ExportResult

        result = exporter.export(simple_markdown)

        assert isinstance(result, ExportResult)

    @needs_weasyprint
    def test_result_content_is_bytes(self, exporter, simple_markdown):
        """Test that result content is bytes."""
        result = exporter.export(simple_markdown)

        assert isinstance(result.content, bytes)

    @needs_weasyprint
    def test_result_is_valid_pdf(self, exporter, simple_markdown):
        """Test that result is a valid PDF (starts with %PDF)."""
        result = exporter.export(simple_markdown)

        assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_result_has_reasonable_size(self, exporter, simple_markdown):
        """Test that generated PDF has a reasonable size."""
        result = exporter.export(simple_markdown)

        # A simple PDF should be at least a few KB
        assert len(result.content) > 1000

    @needs_weasyprint
    def test_result_filename_uses_title(self, exporter, simple_markdown):
        """Test that filename uses provided title."""
        from local_deep_research.exporters import ExportOptions

        options = ExportOptions(title="My Research Report")
        result = exporter.export(simple_markdown, options)

        assert "My_Research_Report" in result.filename
        assert result.filename.endswith(".pdf")

    @needs_weasyprint
    def test_result_filename_default_when_no_title(
        self, exporter, simple_markdown
    ):
        """Test that filename uses default when no title."""
        result = exporter.export(simple_markdown)

        assert "research_report" in result.filename
        assert result.filename.endswith(".pdf")

    @needs_weasyprint
    def test_result_mimetype_is_correct(self, exporter, simple_markdown):
        """Test that result mimetype is correct."""
        result = exporter.export(simple_markdown)

        assert result.mimetype == "application/pdf"

    @needs_weasyprint
    def test_handles_empty_markdown(self, exporter):
        """Test handling of empty markdown."""
        result = exporter.export("")

        assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_handles_markdown_with_all_features(
        self, exporter, sample_markdown
    ):
        """Test handling of markdown with tables, code, lists."""
        result = exporter.export(simple_markdown)

        assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_handles_special_characters(
        self, exporter, markdown_with_special_chars
    ):
        """Test handling of special characters."""
        result = exporter.export(markdown_with_special_chars)

        assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_handles_large_markdown(self, exporter):
        """Test handling of large markdown content."""
        large_content = "# Large Document\n\n"
        large_content += ("This is a paragraph. " * 100 + "\n\n") * 50

        result = exporter.export(large_content)

        assert result.content.startswith(b"%PDF")
        # Large content should produce larger PDF
        assert len(result.content) > 10000

    @needs_weasyprint
    def test_applies_custom_css(self, exporter, simple_markdown):
        """Test that custom CSS can be applied."""
        from local_deep_research.exporters import ExportOptions

        custom_css = "body { font-family: serif; }"
        options = ExportOptions(custom_options={"custom_css": custom_css})
        result = exporter.export(simple_markdown, options)

        assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_logs_pdf_size(self, exporter, simple_markdown):
        """Test that PDF size is logged."""
        with patch(
            "local_deep_research.exporters.pdf_exporter.logger"
        ) as mock_logger:
            exporter.export(simple_markdown)

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert "Generated PDF" in call_args
            assert "bytes" in call_args


class TestPDFExporterIntegration:
    """Integration tests for PDFExporter with ExporterRegistry."""

    def test_registered_in_registry(self):
        """Test that PDFExporter is registered in the registry."""
        from local_deep_research.exporters import ExporterRegistry

        assert ExporterRegistry.is_format_supported("pdf")

    def test_can_get_from_registry(self):
        """Test that PDFExporter can be retrieved from registry."""
        from local_deep_research.exporters import ExporterRegistry
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        exporter = ExporterRegistry.get_exporter("pdf")

        assert isinstance(exporter, PDFExporter)

    @needs_weasyprint
    def test_export_via_registry(self, simple_markdown):
        """Test export via registry lookup."""
        from local_deep_research.exporters import ExporterRegistry

        exporter = ExporterRegistry.get_exporter("pdf")
        result = exporter.export(simple_markdown)

        assert result.content.startswith(b"%PDF")
        assert result.filename.endswith(".pdf")


class TestPDFExporterContentSizeLimit:
    """Tests for content size limit enforcement."""

    @pytest.fixture
    def exporter(self):
        """Create PDFExporter instance."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        return PDFExporter()

    def test_raises_error_for_oversized_content(self, exporter):
        """Test that ValueError is raised for content exceeding size limit."""
        from local_deep_research.exporters.base import BaseExporter

        MAX_CONTENT_SIZE = BaseExporter.MAX_CONTENT_SIZE

        # Create content that exceeds the limit
        oversized_content = "x" * (MAX_CONTENT_SIZE + 1)

        with pytest.raises(ValueError) as exc_info:
            exporter.export(oversized_content)

        assert "exceeds maximum size" in str(exc_info.value)

    @needs_weasyprint
    def test_accepts_content_at_limit(self, exporter):
        """Test that content at exactly the limit is accepted."""
        from local_deep_research.exporters.base import BaseExporter

        MAX_CONTENT_SIZE = BaseExporter.MAX_CONTENT_SIZE

        # Create content at exactly the limit
        content_at_limit = "x" * MAX_CONTENT_SIZE

        mock_service = MagicMock()
        mock_service.markdown_to_pdf.return_value = b"%PDF-1.4 mock content"

        with patch(
            "local_deep_research.exporters.pdf_exporter.get_pdf_service",
            return_value=mock_service,
        ):
            result = exporter.export(content_at_limit)
            assert result.content.startswith(b"%PDF")

    @needs_weasyprint
    def test_accepts_content_under_limit(self, exporter, simple_markdown):
        """Test that content under the limit is accepted."""
        result = exporter.export(simple_markdown)

        assert result.content.startswith(b"%PDF")


class TestPDFExporterErrorHandling:
    """Tests for error handling in PDFExporter."""

    @pytest.fixture
    def exporter(self):
        """Create PDFExporter instance."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        return PDFExporter()

    def test_logs_exception_on_error(self, exporter):
        """Test that exceptions are logged."""
        mock_service = MagicMock()
        mock_service.markdown_to_pdf.side_effect = Exception("Test error")

        with patch(
            "local_deep_research.exporters.pdf_exporter.get_pdf_service",
            return_value=mock_service,
        ):
            with patch(
                "local_deep_research.exporters.pdf_exporter.logger"
            ) as mock_logger:
                with pytest.raises(Exception):
                    exporter.export("test")

                mock_logger.exception.assert_called_once()

    def test_raises_runtime_error_when_weasyprint_missing(self, exporter):
        """Test that a helpful RuntimeError is raised when WeasyPrint is unavailable."""
        with patch(
            "local_deep_research.exporters.pdf_exporter.get_pdf_service",
            side_effect=RuntimeError("PDF export requires WeasyPrint"),
        ):
            with pytest.raises(RuntimeError, match="PDF export requires"):
                exporter.export("test")


class TestPDFExporterFilenameTruncation:
    """Tests for filename truncation with long titles."""

    @pytest.fixture
    def exporter(self):
        """Create PDFExporter instance."""
        from local_deep_research.exporters.pdf_exporter import PDFExporter

        return PDFExporter()

    @needs_weasyprint
    def test_filename_truncated_to_50_chars(self, exporter, simple_markdown):
        """Test that filename is truncated when title exceeds 50 chars."""
        from local_deep_research.exporters import ExportOptions

        # Title with more than 50 characters
        long_title = "A" * 60
        options = ExportOptions(title=long_title)
        result = exporter.export(simple_markdown, options)

        # Filename should be truncated to 50 chars + extension
        filename_without_ext = result.filename.rsplit(".", 1)[0]
        assert len(filename_without_ext) == 50
        assert result.filename.endswith(".pdf")

    @needs_weasyprint
    def test_filename_not_truncated_under_50_chars(
        self, exporter, simple_markdown
    ):
        """Test that filename is not truncated when under 50 chars."""
        from local_deep_research.exporters import ExportOptions

        title = "Short Title"
        options = ExportOptions(title=title)
        result = exporter.export(simple_markdown, options)

        assert "Short_Title" in result.filename
        assert result.filename.endswith(".pdf")


class TestPDFExporterQueryOption:
    """The ``query`` option routes the original research question to
    PDFService for the centred "关于{query}的研究报告" title line.
    PDFExporter is also responsible for NOT prepending the generic H1
    that ``_prepend_title_if_needed`` would otherwise insert — those
    two title surfaces would otherwise compete on the first page.
    """

    @pytest.fixture
    def exporter(self):
        from local_deep_research.exporters.pdf_exporter import PDFExporter
        return PDFExporter()

    @needs_weasyprint
    def test_query_is_forwarded_to_pdf_service(
        self, exporter, simple_markdown, monkeypatch
    ):
        """The ``query`` field on ExportOptions must reach the
        ``markdown_to_pdf`` call so PDFService can render the title
        line. We assert this without parsing the rendered PDF by
        intercepting the call.
        """
        from local_deep_research.exporters import ExportOptions
        from local_deep_research.web.services import pdf_service as pdf_mod

        captured = {}

        def spy_markdown_to_pdf(
            self, markdown_content, title=None, metadata=None,
            custom_css=None, query=None, base_url=None,
            trusted_hosts=None,
        ):
            captured["query"] = query
            captured["title"] = title
            captured["markdown_content"] = markdown_content
            captured["base_url"] = base_url
            return b"%PDF-1.4\n%fake"

        monkeypatch.setattr(
            pdf_mod.PDFService, "markdown_to_pdf", spy_markdown_to_pdf
        )

        options = ExportOptions(title="Some Title", query="量子计算简介")
        exporter.export(simple_markdown, options)

        assert captured["query"] == "量子计算简介"

    @needs_weasyprint
    def test_query_none_omits_title_line(self, exporter, simple_markdown, monkeypatch):
        """If the route does not pass a query (e.g. legacy callers),
        the PDF path still works — no title line is rendered.
        """
        from local_deep_research.exporters import ExportOptions
        from local_deep_research.web.services import pdf_service as pdf_mod

        captured = {}

        def spy_markdown_to_pdf(
            self, markdown_content, title=None, metadata=None,
            custom_css=None, query=None, base_url=None,
            trusted_hosts=None,
        ):
            captured["query"] = query
            captured["base_url"] = base_url
            return b"%PDF-1.4\n%fake"

        monkeypatch.setattr(
            pdf_mod.PDFService, "markdown_to_pdf", spy_markdown_to_pdf
        )

        options = ExportOptions(title="Some Title")  # no query
        exporter.export(simple_markdown, options)

        assert captured["query"] is None

    @needs_weasyprint
    def test_does_not_prepend_h1(
        self, exporter, sample_markdown, monkeypatch
    ):
        """PDFExporter must skip the base-class H1 prepend so the
        custom title line rendered by PDFService has no H1 competitor.
        Other exporters (ODT) still get the H1 — this is a PDF-only
        decision because only PDF renders the centred Chinese title.
        """
        from local_deep_research.exporters import ExportOptions
        from local_deep_research.web.services import pdf_service as pdf_mod

        captured = {}

        def spy_markdown_to_pdf(
            self, markdown_content, title=None, metadata=None,
            custom_css=None, query=None, base_url=None,
            trusted_hosts=None,
        ):
            # The markdown that reaches PDFService must NOT start with
            # "# Some Title" (which would be the base-class prepend).
            captured["first_line"] = markdown_content.split("\n", 1)[0]
            captured["base_url"] = base_url
            return b"%PDF-1.4\n%fake"

        monkeypatch.setattr(
            pdf_mod.PDFService, "markdown_to_pdf", spy_markdown_to_pdf
        )

        options = ExportOptions(title="Some Title", query="x")
        exporter.export(sample_markdown, options)

        assert not captured["first_line"].startswith("# Some Title"), (
            "PDFExporter must NOT prepend the H1 title — the custom "
            "title line rendered by PDFService would otherwise compete "
            "with it on the first page"
        )


    @needs_weasyprint
    def test_base_url_is_forwarded_to_pdf_service(
        self, exporter, simple_markdown, monkeypatch
    ):
        """``base_url`` must reach ``markdown_to_pdf`` so WeasyPrint can
        resolve the report's local ``/images/...`` routes. Without
        this, every image fetch is rejected by the SSRF guard and the
        exported PDF silently has no images.
        """
        from local_deep_research.exporters import ExportOptions
        from local_deep_research.web.services import pdf_service as pdf_mod

        captured = {}

        def spy_markdown_to_pdf(
            self, markdown_content, title=None, metadata=None,
            custom_css=None, query=None, base_url=None,
            trusted_hosts=None,
        ):
            captured["base_url"] = base_url
            return b"%PDF-1.4\nfake"

        monkeypatch.setattr(
            pdf_mod.PDFService, "markdown_to_pdf", spy_markdown_to_pdf
        )

        options = ExportOptions(
            title="t", query="q", base_url="http://example.com/"
        )
        exporter.export(simple_markdown, options)

        assert captured["base_url"] == "http://example.com/"

    @needs_weasyprint
    def test_base_url_none_when_not_provided(
        self, exporter, simple_markdown, monkeypatch
    ):
        """When the caller doesn't pass a base_url, PDFService falls
        back to its DEFAULT_BASE_URL — we just need to make sure the
        value flows through unchanged.
        """
        from local_deep_research.exporters import ExportOptions
        from local_deep_research.web.services import pdf_service as pdf_mod

        captured = {}

        def spy_markdown_to_pdf(
            self, markdown_content, title=None, metadata=None,
            custom_css=None, query=None, base_url=None,
            trusted_hosts=None,
        ):
            captured["base_url"] = base_url
            return b"%PDF-1.4\nfake"

        monkeypatch.setattr(
            pdf_mod.PDFService, "markdown_to_pdf", spy_markdown_to_pdf
        )

        # ExportOptions default for base_url is None.
        options = ExportOptions(title="t", query="q")
        exporter.export(simple_markdown, options)

        assert captured["base_url"] is None
