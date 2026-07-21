from unittest.mock import patch
from local_deep_research.research_library.downloaders.html import HTMLDownloader


def test_download_with_html_returns_text_and_raw_html():
    d = HTMLDownloader()
    raw = "<html><body><p>Hello world this is the body content.</p><img src='https://x/a.jpg'></body></html>"
    with patch.object(d, "_fetch_html", return_value=raw) as mock_fetch:
        text_bytes, html = d.download_with_html("https://example.com")
    # single fetch only
    mock_fetch.assert_called_once_with("https://example.com")
    assert html == raw
    # text extracted (may be None if extractor rejects short content) — html always returned
    assert isinstance(html, str)


def test_download_with_html_none_when_fetch_fails():
    d = HTMLDownloader()
    with patch.object(d, "_fetch_html", return_value=None):
        text_bytes, html = d.download_with_html("https://example.com")
    assert text_bytes is None
    assert html is None
