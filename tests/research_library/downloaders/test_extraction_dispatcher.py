from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction import pipeline


def test_default_config_playwright_only_text():
    """Default config (use_for_content_fetch=false, enable_images=false):
    dispatcher returns text from Playwright download_with_html; images=[]."""
    raw = "<html><body><p>Hello world this is the body content.</p></body></html>"
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={}, enable_images=False
        )

    entry = out["https://src/p"]
    assert entry["text"] == "body text"
    assert entry["images"] == []
    fake_dl.download_with_html.assert_called_once_with("https://src/p")