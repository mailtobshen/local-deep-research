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