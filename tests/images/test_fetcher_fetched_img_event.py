"""Tests for the per-image FETCHED_IMG IMG-TRACE event.

The user-facing requirement (re-raised 2026-08-04): the fetcher's
per-page ``url=...`` line counts images but the per-image inventory
is missing. This test asserts the new ``FETCHED_IMG`` event carries
the same five-key schema as the rest of the IMG-TRACE pipeline so a
single grep over the log reconstructs the (alt, img_url, source_url)
tuple for every image the page ever offered — not just the ones
that later pass the citation-anchored gate.
"""

from unittest.mock import MagicMock, patch

from local_deep_research.research_library.downloaders.extraction import pipeline


HTML = (
    '<html><body>'
    '<img src="/a.jpg" alt="Canton Tower" width="800" height="600">'
    '<img src="/b.jpg" alt="Pearl Tower" width="1200" height="900">'
    '</body></html>'
)


def test_fetched_img_event_per_image(loguru_caplog):
    """Each extracted image must surface a FETCHED_IMG line with the
    five-key vocabulary (cite_num / ref_url are ``-`` at fetch time
    because the image hasn't been bound to a citation yet)."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body", HTML)
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        pipeline.fetch_content_with_images(
            ["https://src/page"], titles={"https://src/page": "Page"}
        )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    lines = [line for line in text.splitlines()
             if "[IMG-TRACE] FETCHED_IMG" in line]
    assert len(lines) == 2, f"expected 2 FETCHED_IMG lines, got {len(lines)}"
    alts = []
    for line in lines:
        for key in ("src_url", "img_alt", "img_url", "img_source_url",
                    "cite_num", "ref_url"):
            assert f"{key}=" in line, f"missing {key}=: {line!r}"
        # cite_num / ref_url are unknown at fetch time, recorded as ``-``.
        assert "cite_num=-" in line
        assert "ref_url=-" in line
        # Parse the alt value (could contain spaces — pull between
        # the first single-quote after ``img_alt=`` and the next).
        idx = line.find("img_alt='")
        assert idx >= 0
        end = line.find("'", idx + len("img_alt='"))
        assert end > 0
        alts.append(line[idx + len("img_alt='"):end])
    # Both alts present, order-preserving.
    assert "Canton Tower" in alts
    assert "Pearl Tower" in alts


def test_fetched_img_uses_absolute_urls(loguru_caplog):
    """``img_url`` on FETCHED_IMG must be the absolute URL the fetcher
    resolved (``urljoin(source_url, src)``) so the log lines are
    self-sufficient for offline image discovery."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body", HTML)
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        pipeline.fetch_content_with_images(
            ["https://src/page"], titles={"https://src/page": "Page"}
        )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    lines = [line for line in text.splitlines()
             if "[IMG-TRACE] FETCHED_IMG" in line]
    urls = [line.split("img_url=")[1].split(" ")[0] for line in lines]
    assert "https://src/a.jpg" in urls
    assert "https://src/b.jpg" in urls
