"""Base classes for document exporters.

This module provides the abstract base class and data structures that all
exporters must implement to participate in the export system.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExportResult:
    """Result of an export operation."""

    content: bytes
    filename: str
    mimetype: str


@dataclass
class ExportOptions:
    """Common options for all exporters.

    Attributes:
        title: Optional document title (used as <title> and possibly H1)
        query: Optional original research query used by PDF export to
            render the centred "关于{query}的研究报告" title line
            (二号 黑体 居中). Ignored by exporters that don't need it.
        base_url: Origin URL the PDF exporter forwards to WeasyPrint so
            relative image routes (e.g. ``/images/<research_id>/<filename>``
            after ``images.store.rewrite_markdown``) resolve to the
            Flask app. Without this, those fetches fail the SSRF
            check and the PDF silently drops every image. Defaults
            inside PDFService.DEFAULT_BASE_URL when None.
        trusted_hosts: Extra host suffixes the PDF url_fetcher should
            trust beyond loopback — e.g. the Flask ``request.host``
            for a private-network deployment where the WebUI is
            reached via a LAN IP. Cloud-metadata IPs are always
            blocked regardless.
        metadata: Optional metadata dict (author, date, etc.)
        custom_options: Format-specific options (e.g., custom_css for PDF)
    """

    title: Optional[str] = None
    query: Optional[str] = None
    base_url: Optional[str] = None
    trusted_hosts: Optional[tuple] = None
    metadata: Optional[Dict[str, Any]] = None
    custom_options: Optional[Dict[str, Any]] = field(default_factory=dict)


class BaseExporter(ABC):
    """Abstract base class for document exporters.

    All exporters must inherit from this class and implement the required
    abstract methods to participate in the export registry.

    Example:
        class MyExporter(BaseExporter):
            @property
            def format_name(self) -> str:
                return "myformat"

            @property
            def file_extension(self) -> str:
                return ".myf"

            @property
            def mimetype(self) -> str:
                return "application/x-myformat"

            def export(self, markdown_content, options=None) -> ExportResult:
                # Implementation here
                ...
    """

    # Maximum content size (50 MB) to prevent OOM errors
    MAX_CONTENT_SIZE = 50 * 1024 * 1024

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Return the format identifier (e.g., 'pdf', 'docx', 'latex').

        This is used to look up the exporter in the registry.
        """
        pass

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """Return the file extension including the dot (e.g., '.pdf', '.docx')."""
        pass

    @property
    @abstractmethod
    def mimetype(self) -> str:
        """Return the MIME type for the exported file."""
        pass

    def _validate_content_size(self, content: str) -> None:
        """Validate that content does not exceed the maximum size limit.

        Args:
            content: The content string to validate

        Raises:
            ValueError: If content exceeds MAX_CONTENT_SIZE
        """
        if len(content) > self.MAX_CONTENT_SIZE:
            raise ValueError(
                f"Content exceeds maximum size of "
                f"{self.MAX_CONTENT_SIZE // (1024 * 1024)} MB"
            )

    @abstractmethod
    def export(
        self,
        markdown_content: str,
        options: Optional[ExportOptions] = None,
    ) -> ExportResult:
        """Export markdown content to the target format.

        Args:
            markdown_content: The markdown text to convert
            options: Optional export options

        Returns:
            ExportResult with content bytes, filename, and mimetype
        """
        pass

    def _generate_safe_filename(self, title: Optional[str]) -> str:
        """Generate a safe filename from the title.

        Args:
            title: Optional title to use in the filename

        Returns:
            A sanitized filename with the appropriate extension
        """
        if title:
            safe_title = (
                re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")[:50]
            )
        else:
            safe_title = "research_report"
        return f"{safe_title}{self.file_extension}"

    def _prepend_title_if_needed(
        self, content: str, title: Optional[str]
    ) -> str:
        """Prepend title as H1 heading if content doesn't already have one.

        This method is used by exporters that render markdown documents
        (like PDF and DOCX) to ensure the title appears in the output.
        Exporters that don't render documents (like RIS) should not use this.

        Args:
            content: The markdown content
            title: Optional title to prepend

        Returns:
            Content with title prepended if needed, otherwise unchanged
        """
        if not title:
            return content
        # Don't prepend if content already starts with this title
        if content.startswith(f"# {title}"):
            return content
        # Only prepend if content doesn't start with any heading
        if not content.lstrip().startswith("#"):
            return f"# {title}\n\n{content}"
        return content
