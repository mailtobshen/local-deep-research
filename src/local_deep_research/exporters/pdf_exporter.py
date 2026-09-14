"""PDF export service wrapper.

This module provides the PDFExporter class that wraps the existing
PDFService implementation from web/services/pdf_service.py.

The existing PDFService is kept unchanged - this is just a wrapper
to integrate it with the modular exporter registry.
"""

from typing import Optional

from loguru import logger

from ..web.services.pdf_service import get_pdf_service
from .base import BaseExporter, ExportOptions, ExportResult
from .registry import ExporterRegistry


@ExporterRegistry.register
class PDFExporter(BaseExporter):
    """PDF exporter wrapping the existing PDFService implementation.

    This exporter delegates to the existing PDFService which uses
    WeasyPrint for PDF generation. No changes are made to the
    underlying PDF generation logic.
    """

    def __init__(self):
        """Initialize PDF exporter.

        Note: pdf_service import is deferred to export() so that
        registration succeeds even when WeasyPrint system libraries
        (Pango, Cairo, GLib) are not installed.
        """

    @property
    def format_name(self) -> str:
        return "pdf"

    @property
    def file_extension(self) -> str:
        return ".pdf"

    @property
    def mimetype(self) -> str:
        return "application/pdf"

    def export(
        self,
        markdown_content: str,
        options: Optional[ExportOptions] = None,
    ) -> ExportResult:
        """Convert markdown content to PDF using existing PDFService.

        Args:
            markdown_content: The markdown text to convert
            options: Optional export options (title, metadata, custom_css)

        Returns:
            ExportResult with PDF content as bytes, filename, and mimetype

        Raises:
            ValueError: If content exceeds maximum size limit
        """
        try:
            # Check content size limit to prevent OOM errors
            self._validate_content_size(markdown_content)

            pdf_service = get_pdf_service()

            options = options or ExportOptions()

            # Intentionally skip the base-class H1 prepend for PDF:
            # PDFService._markdown_to_html builds its own centred
            # "关于{query}的研究报告" title line from ``options.query``,
            # which would compete with the generic H1 that
            # _prepend_title_if_needed would otherwise insert. Other
            # exporters (ODT) still get the H1 prepend because they do
            # not render a custom title.

            # Extract custom_css if provided
            custom_css = None
            if options.custom_options:
                custom_css = options.custom_options.get("custom_css")

            # Delegate to existing PDFService
            pdf_bytes = pdf_service.markdown_to_pdf(
                markdown_content,
                title=options.title,
                metadata=options.metadata,
                custom_css=custom_css,
                query=options.query,
                base_url=options.base_url,
                trusted_hosts=options.trusted_hosts,
            )

            filename = self._generate_safe_filename(options.title)

            logger.info(
                f"Generated PDF in memory, size: {len(pdf_bytes)} bytes"
            )

            return ExportResult(
                content=pdf_bytes,
                filename=filename,
                mimetype=self.mimetype,
            )

        except Exception:
            logger.exception("Error generating PDF")
            raise
