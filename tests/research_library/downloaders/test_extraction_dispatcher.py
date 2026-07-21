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


def test_enable_images_extracts_from_playwright_html():
    """When enable_images=True and Playwright succeeds, images come from
    Playwright's raw_html."""
    raw = '<html><body><img src="https://real/a.jpg" alt="tower" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"],
            titles={"https://src/p": "Page"},
            settings_snapshot={},
            enable_images=True,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "body text"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    assert entry["images"][0].source_title == "Page"


def test_playwright_fails_firecrawl_fallback_text_only():
    """When Playwright returns no text and firecrawl is enabled,
    dispatcher calls FirecrawlClient.scrape(link, include_html=False)
    and uses markdown as text; images=[]."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    fake_client = MagicMock()
    fake_client.scrape.return_value = {"markdown": "# hi", "html": "<html></html>"}

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_new_firecrawl_client_from_snapshot", return_value=fake_client), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=True):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={"any": "snapshot"},
            enable_images=False,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "# hi"
    assert entry["images"] == []
    fake_client.scrape.assert_called_once_with("https://src/p", include_html=False)


def test_playwright_fails_firecrawl_fallback_with_images():
    """When Playwright returns no text, firecrawl is enabled, AND
    enable_images=True, dispatcher calls scrape(include_html=True) and
    extracts images from the returned html."""
    raw_html = '<html><body><img src="https://real/a.jpg" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    fake_client = MagicMock()
    fake_client.scrape.return_value = {"markdown": "# hi", "html": raw_html}

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_new_firecrawl_client_from_snapshot", return_value=fake_client), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=True):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={"any": "snapshot"},
            enable_images=True,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "# hi"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    fake_client.scrape.assert_called_once_with("https://src/p", include_html=True)


def test_playwright_fails_firecrawl_disabled_returns_none():
    """When Playwright returns no text and firecrawl is NOT enabled,
    dispatcher returns {text: None, images: []} (no fallback)."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=False):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={}, enable_images=False,
        )

    assert out["https://src/p"] == {"text": None, "images": []}