from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction import pipeline


def test_returns_text_and_images_from_single_fetch():
    raw = '<html><body><img src="https://real/a.jpg" alt="tower" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline.fetch_content_with_images(
            ["https://src/page"], titles={"https://src/page": "Page"}
        )
    entry = out["https://src/page"]
    assert entry["text"] == "body text"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    assert entry["images"][0].source_title == "Page"
    fake_dl.download_with_html.assert_called_once_with("https://src/page")


def test_image_extraction_failure_does_not_break_text():
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", "<html>ok</html>")
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "extract_images", side_effect=Exception("bs4 boom")):
        out = pipeline.fetch_content_with_images(["https://src/page"])
    entry = out["https://src/page"]
    assert entry["text"] == "body text"
    assert entry["images"] == []


def test_empty_urls_returns_empty():
    assert pipeline.fetch_content_with_images([]) == {}


def test_lazy_resolution_when_module_attr_is_none():
    """If module-level placeholder is None (deferred), real call still resolves the class."""
    from local_deep_research.research_library.downloaders import playwright_html

    # Force the failure mode: pipeline.AutoHTMLDownloader stuck at None
    original = pipeline.AutoHTMLDownloader
    pipeline.AutoHTMLDownloader = None
    try:
        # Use a real downloader instance (no full network — just verify resolution works)
        # Easiest: monkeypatch AutoHTMLDownloader on the playwright_html module
        fake_instance = MagicMock()
        fake_instance.download_with_html.return_value = (b"text", "<html></html>")
        with patch.object(
            playwright_html, "AutoHTMLDownloader", return_value=fake_instance
        ) as cls_mock:
            out = pipeline.fetch_content_with_images(["https://x/page"])
        # Real class was resolved and called
        cls_mock.assert_called_once()
        assert out["https://x/page"]["text"] == "text"
    finally:
        pipeline.AutoHTMLDownloader = original