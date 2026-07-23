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


def test_full_search_gate_on_sets_html_content():
    from local_deep_research.web_search_engines.engines.full_search import (
        FullSearchResults,
    )
    from local_deep_research.images.extractor import ExtractedImage

    fs = FullSearchResults(llm=None, web_search=MagicMock(), settings_snapshot={})
    items = [{"link": "https://src/p", "title": "P"}]
    img = ExtractedImage(url="https://real/a.jpg", alt="a", source_url="s", source_title="P", width=None, height=None)

    with patch("local_deep_research.web_search_engines.engines.full_search.get_bool_setting_from_snapshot", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.validate_url", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content_with_images",
               return_value={"https://src/p": {"text": "body", "images": [img]}}):
        out = fs._get_full_content(items)
    import json
    parsed = json.loads(out[0]["html_content"])
    assert parsed[0]["url"] == "https://real/a.jpg"
    assert out[0]["full_content"] == "body"


def test_full_search_gate_off_uses_plain_fetch_content():
    from local_deep_research.web_search_engines.engines.full_search import (
        FullSearchResults,
    )

    fs = FullSearchResults(llm=None, web_search=MagicMock(), settings_snapshot={})
    items = [{"link": "https://src/p", "title": "P"}]
    with patch("local_deep_research.web_search_engines.engines.full_search.get_bool_setting_from_snapshot", return_value=False), \
         patch("local_deep_research.web_search_engines.engines.full_search.validate_url", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content", return_value={"https://src/p": "body"}) as fc, \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content_with_images") as fcwi:
        out = fs._get_full_content(items)
    fc.assert_called_once()
    fcwi.assert_not_called()
    assert out[0]["full_content"] == "body"
    assert "html_content" not in out[0]  # type: ignore[index]  # noqa: E501


def test_wrapper_delegates_to_dispatcher_with_enable_images_true():
    from local_deep_research.research_library.downloaders.extraction import pipeline

    expected = {
        "https://src/p": {"text": "body", "images": ["img1"]},
    }
    with patch.object(
        pipeline, "_fetch_content_dispatcher", return_value=expected
    ) as mock_d:
        out = pipeline.fetch_content_with_images(
            ["https://src/p"],
            titles={"https://src/p": "Page"},
            settings_snapshot={"k": "v"},
        )
    mock_d.assert_called_once_with(
        ["https://src/p"],
        titles={"https://src/p": "Page"},
        settings_snapshot={"k": "v"},
        language="English",
        enable_js_rendering=False,
        enable_images=True,
    )
    assert out is expected
