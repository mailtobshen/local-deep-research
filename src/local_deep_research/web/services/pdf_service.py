"""
PDF generation service using WeasyPrint.

Based on deep research findings, WeasyPrint is the optimal choice for
production Flask applications due to:
- Pure Python (no external binaries except Pango)
- Modern CSS3 support
- Active maintenance (v66.0 as of July 2025)
- Good paged media features
"""

import base64
import concurrent.futures
import io
import platform
from html import escape
from typing import Optional, Dict, Any
import markdown  # type: ignore[import-untyped]
from loguru import logger

try:
    from weasyprint import HTML, CSS
    from weasyprint.urls import URLFetcher

    WEASYPRINT_AVAILABLE = True
except (OSError, ImportError) as _weasyprint_err:
    HTML = None  # type: ignore[assignment,misc]
    CSS = None  # type: ignore[assignment,misc]
    URLFetcher = None  # type: ignore[assignment,misc]
    WEASYPRINT_AVAILABLE = False
    logger.warning("WeasyPrint not available — PDF export will be disabled")

from ...security import validate_url


_WEASYPRINT_DOCS_URL = (
    "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
)


class UnsafePDFResourceURLError(ValueError):
    """Subclasses ValueError so WeasyPrint skips the resource instead of aborting the render."""


# Module-level URLFetcher preserves the allow_redirects=False posture that
# default_url_fetcher hard-coded. Redirects disabled keeps the SSRF guard
# airtight — validate_url only inspects the initial URL, so a 30x to a
# cloud metadata endpoint (see ssrf_validator.ALWAYS_BLOCKED_METADATA_IPS)
# would otherwise slip past.
_URL_FETCHER = (
    URLFetcher(allow_redirects=False) if WEASYPRINT_AVAILABLE else None
)

# 5 s matches the client-side loadImageForPdf timeout (pdf.js). Long enough
# for slow CDNs, short enough that a hung image cannot freeze the whole
# "Download PDF" click. Only the WeasyPrint worker thread is impacted —
# the Flask request itself stays responsive because the timeout is enforced
# in a single-shot executor that returns a placeholder on expiry.
_PDF_RESOURCE_FETCH_TIMEOUT_S = 5

# 1×1 transparent PNG. WeasyPrint embeds this wherever an image failed to
# load, so the PDF reader sees an invisible 1×1 instead of a missing-image
# gap. Bytes produced by Pillow — verified to round-trip via Image.open
# + base64.b64decode (the previous hand-written bytes were rejected by
# Pillow as "Truncated File Read" because the IDAT chunk was malformed).
_PLACEHOLDER_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)

_IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
)


def _looks_like_image_url(url: str) -> bool:
    """Best-effort URL-extension check.

    True when the URL (sans query / fragment) ends in a known image
    extension. Used to decide whether a fetch failure should fall back
    to the placeholder PNG (images) or propagate the error (CSS / JS /
    fonts — we do not want to silently swallow bundling bugs).
    """
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return any(path.endswith(ext) for ext in _IMAGE_EXTS)


def _placeholder_image_response() -> Dict[str, Any]:
    """Return a WeasyPrint url_fetcher-shaped mapping for the 1×1 PNG."""
    return {"string": _PLACEHOLDER_PNG_BYTES, "mime_type": "image/png"}


def _safe_url_fetcher(url):
    """WeasyPrint url_fetcher that:

    1. Blocks SSRF targets via ``validate_url`` (GHSA-fj2m-qvh9-jq4q).
    2. Bounds every external fetch at :data:`_PDF_RESOURCE_FETCH_TIMEOUT_S`
       so a hung CDN cannot freeze the whole PDF render — parity with
       the client-side ``loadImageForPdf`` helper.
    3. On fetch failure for image URLs, returns the 1×1 transparent PNG
       placeholder so the rendered PDF has an invisible gap instead of
       a missing-image gap. For non-image resources (CSS / JS / fonts)
       the failure propagates so WeasyPrint logs the missing resource
       and bundling / configuration bugs are not silently masked.
    """
    if not validate_url(url):
        logger.warning(f"Blocked unsafe URL in PDF rendering: {url}")
        raise UnsafePDFResourceURLError(
            f"Blocked unsafe URL in PDF rendering: {url}"
        )

    is_image = _looks_like_image_url(url)

    # A fresh executor per call is intentional — a one-shot timeout must
    # release its thread once the result (or timeout) is settled. The
    # underlying WeasyPrint URLFetcher is thread-safe.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_URL_FETCHER.fetch, url)
        try:
            return future.result(timeout=_PDF_RESOURCE_FETCH_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            if is_image:
                logger.warning(
                    f"PDF image fetch timed out after "
                    f"{_PDF_RESOURCE_FETCH_TIMEOUT_S}s, using placeholder: {url}"
                )
                return _placeholder_image_response()
            logger.warning(
                f"PDF resource fetch timed out after "
                f"{_PDF_RESOURCE_FETCH_TIMEOUT_S}s: {url}"
            )
            raise
        except Exception:
            if is_image:
                logger.warning(
                    f"PDF image fetch failed, using placeholder: {url}"
                )
                return _placeholder_image_response()
            raise


class MissingPDFDependencyError(RuntimeError):
    """Raised when WeasyPrint system libraries are unavailable.

    Distinct from generic RuntimeError so the web layer can surface this
    message to users without also exposing unrelated RuntimeErrors
    (e.g., pandoc subprocess stderr from ODT export).
    """


def get_weasyprint_install_instructions() -> str:
    """Return platform-specific install instructions for WeasyPrint system deps."""
    system = platform.system()
    if system == "Darwin":
        return (
            "PDF export requires WeasyPrint system libraries (Pango, Cairo, GLib).\n"
            "Install with: brew install weasyprint\n"
            f"See: {_WEASYPRINT_DOCS_URL}#macos"
        )
    if system == "Linux":
        return (
            "PDF export requires WeasyPrint system libraries (Pango, Cairo, GLib).\n"
            f"See: {_WEASYPRINT_DOCS_URL}#linux"
        )
    if system == "Windows":
        return (
            "PDF export requires Pango system libraries.\n"
            f"See: {_WEASYPRINT_DOCS_URL}#windows"
        )
    return (
        "PDF export requires WeasyPrint system libraries (Pango, Cairo, GLib).\n"
        f"See: {_WEASYPRINT_DOCS_URL}"
    )


# Default CSS body kept as a module-level constant so tests can
# introspect the rules (WeasyPrint's CSS object strips the source string
# after parsing). The PDFService constructor feeds this same constant
# into weasyprint.CSS below, so they stay in lock-step.
_MINIMAL_CSS_BODY = """
            @page {
                size: A4;
                margin: 1.5cm;
                /* 五号 宋体 居中页码 — renders on every page via WeasyPrint. */
                @bottom-center {
                    content: "第" counter(page) "页/共" counter(pages) "页";
                    font-family: "SimSun", "Songti SC", "Noto Serif CJK SC",
                        "Source Han Serif SC", serif;
                    font-size: 9pt;
                    color: #666;
                }
            }

            /* 二号 黑体 居中 — prepended by _markdown_to_html when the
               caller passes the original research query. */
            .ldr-pdf-title {
                font-family: SimHei, "Heiti SC", "Noto Sans CJK SC",
                    "Source Han Sans SC", "Microsoft YaHei", sans-serif;
                font-size: 22pt;
                font-weight: bold;
                text-align: center;
                margin: 1.5em 0 1em 0;
                line-height: 1.3;
            }

            body {
                font-family: Arial, "Noto Sans CJK SC", "Noto Sans CJK TC",
                    "Noto Sans CJK JP", "Noto Sans CJK KR", "Noto Sans SC",
                    "PingFang SC", "PingFang TC", "Hiragino Sans",
                    "Hiragino Kaku Gothic ProN", "Apple SD Gothic Neo",
                    "Microsoft YaHei", "Microsoft JhengHei",
                    "Yu Gothic", "Malgun Gothic", "SimSun", sans-serif;
                font-size: 10pt;
                line-height: 1.4;
            }

            table {
                border-collapse: collapse;
                width: 100%;
                margin: 0.5em 0;
            }

            th, td {
                border: 1px solid #ccc;
                padding: 6px;
                text-align: left;
            }

            th {
                background-color: #f0f0f0;
            }

            h1 { font-size: 16pt; margin: 0.5em 0; }
            h2 { font-size: 14pt; margin: 0.5em 0; }
            h3 { font-size: 12pt; margin: 0.5em 0; }
            h4 { font-size: 11pt; margin: 0.5em 0; font-weight: bold; }
            h5 { font-size: 10pt; margin: 0.5em 0; font-weight: bold; }
            h6 { font-size: 10pt; margin: 0.5em 0; }

            code, pre {
                font-family: monospace, "Noto Sans Mono CJK SC",
                    "Noto Sans Mono CJK TC", "Noto Sans Mono CJK JP",
                    "Noto Sans Mono CJK KR", "Noto Sans CJK SC",
                    "PingFang SC", "Hiragino Sans", "Apple SD Gothic Neo",
                    "Microsoft YaHei", "SimSun";
                background-color: #f5f5f5;
            }

            code {
                padding: 1px 3px;
            }

            pre {
                padding: 8px;
                overflow-x: auto;
            }

            a {
                color: #0066cc;
                text-decoration: none;
            }
        """


class PDFService:
    """Service for converting markdown to PDF using WeasyPrint."""

    #: Raw source of the default CSS. Mirrors ``_MINIMAL_CSS_BODY`` so
    #: tests can introspect rules without depending on WeasyPrint
    #: internals. Keep in sync with the constructor below.
    MINIMAL_CSS_SOURCE = _MINIMAL_CSS_BODY

    def __init__(self):
        """Initialize PDF service with minimal CSS for readability."""
        # CJK families are listed as fallbacks so WeasyPrint substitutes a
        # glyph-bearing font when the primary stack lacks coverage. Without
        # this, Chinese/Japanese/Korean text disappears silently from the
        # PDF even though it renders fine in the HTML view (issue #4055).
        # Glyphs still require the corresponding system font (e.g.
        # fonts-noto-cjk) to actually be installed.
        self.minimal_css = CSS(string=_MINIMAL_CSS_BODY)

    def markdown_to_pdf(
        self,
        markdown_content: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        custom_css: Optional[str] = None,
        query: Optional[str] = None,
    ) -> bytes:
        """
        Convert markdown content to PDF.

        Args:
            markdown_content: The markdown text to convert
            title: Optional title for the document
            metadata: Optional metadata dict (author, date, etc.)
            custom_css: Optional CSS string to override defaults
            query: Optional original research query used to build the
                centred "关于{query}的研究报告" title line at the top
                of the document. Distinct from ``title`` (which only
                populates ``<title>``); the title line is rendered with
                the .ldr-pdf-title CSS rule (二号 黑体 居中).

        Returns:
            PDF file as bytes

        Note:
            WeasyPrint memory usage can spike with large documents.
            Production deployments should implement:
            - Memory limits (ulimit)
            - Timeouts (30-60 seconds)
            - Worker recycling after 100 requests
        """
        try:
            # Convert markdown to HTML
            html_content = self._markdown_to_html(
                markdown_content, title, metadata, query
            )

            # url_fetcher blocks SSRF targets reachable via body/citation URLs.
            html_doc = HTML(string=html_content, url_fetcher=_safe_url_fetcher)

            # Apply CSS (custom or minimal default)
            css_list = []
            if custom_css:
                css_list.append(CSS(string=custom_css))
            else:
                css_list.append(self.minimal_css)

            # Generate PDF
            # Use BytesIO to get bytes instead of writing to file
            pdf_buffer = io.BytesIO()
            html_doc.write_pdf(pdf_buffer, stylesheets=css_list)

            # Get the PDF bytes
            pdf_bytes = pdf_buffer.getvalue()
            pdf_buffer.close()

            logger.info(f"Generated PDF, size: {len(pdf_bytes)} bytes")
            return pdf_bytes

        except Exception:
            logger.exception("Error generating PDF")
            raise

    def _markdown_to_html(
        self,
        markdown_content: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        query: Optional[str] = None,
    ) -> str:
        """
        Convert markdown to HTML with proper structure.

        Uses Python-Markdown with extensions for:
        - Tables
        - Fenced code blocks
        - Table of contents
        - Footnotes

        If ``query`` is provided, prepends a centred title div
        ``<div class="ldr-pdf-title">关于{query}的研究报告</div>`` to
        the body, BEFORE any TOC / chapter heading. Styled by the
        .ldr-pdf-title CSS rule (二号 黑体 居中). The query text is
        HTML-escaped — defence in depth, even though this string comes
        from our own database.
        """
        # Parse markdown with extensions
        md = markdown.Markdown(
            extensions=[
                "tables",
                "fenced_code",
                "footnotes",
                "toc",
                "nl2br",  # Convert newlines to <br>
                "sane_lists",
                "meta",
            ]
        )

        html_body = md.convert(markdown_content)

        # Build complete HTML document
        html_parts = ["<!DOCTYPE html><html><head>"]
        html_parts.append('<meta charset="utf-8">')

        if title:
            html_parts.append(f"<title>{escape(title)}</title>")

        if metadata:
            for key, value in metadata.items():
                html_parts.append(
                    f'<meta name="{escape(str(key))}" content="{escape(str(value))}">'
                )

        html_parts.append("</head><body>")

        # Insert the centred title line BEFORE the markdown body so it
        # sits ahead of any TOC / chapter heading produced by the report.
        if query:
            html_parts.append(
                f'<div class="ldr-pdf-title">关于{escape(query)}的研究报告</div>'
            )

        # Add the markdown content directly without any extra title or metadata
        html_parts.append(html_body)

        # (No body footer — the page-number footer is rendered via the
        # @page rule in minimal_css. The previous "Generated by LDR..."
        # block was removed because it duplicated project branding inside
        # the report itself.)

        html_parts.append("</body></html>")

        return "".join(html_parts)


# Singleton instance
_pdf_service = None


def get_pdf_service() -> PDFService:
    """Get or create the PDF service singleton.

    Raises:
        MissingPDFDependencyError: If WeasyPrint system libraries are not
            available, with platform-specific installation instructions.
    """
    if not WEASYPRINT_AVAILABLE:
        raise MissingPDFDependencyError(get_weasyprint_install_instructions())
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service
