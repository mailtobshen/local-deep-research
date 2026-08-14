"""Task 4: _fetch_with_playwright launches a SOCKS5-routed browser when the
target is a .onion URL, and uses the regular browser otherwise.

This test mocks the Playwright API surface so it stays hermetic and
does not need a real .onion connection.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def downloader():
    from local_deep_research.research_library.downloaders.playwright_html import (
        PlaywrightHTMLDownloader,
    )

    dl = PlaywrightHTMLDownloader(timeout=10, language="en")
    yield dl
    dl.close()


def _patch_pw(mock_browser_factory):
    """Patch playwright.sync_api.sync_playwright so _fetch_with_playwright
    sees our mocks instead of launching a real Chromium.

    Returns a tuple (sync_api_patch, chromium_launch_mock) so the test
    can inspect which browser got launched for which URL.
    """
    pw_mock = MagicMock()
    # `start()` returns the same pw_mock so that subsequent
    # `pw.chromium.launch(...)` calls land on our side_effect.
    pw_mock.start.return_value = pw_mock
    pw_mock.chromium.launch = MagicMock(side_effect=mock_browser_factory)
    sync_playwright_mock = MagicMock(return_value=pw_mock)
    return pw_mock, sync_playwright_mock


def test_onion_url_uses_socks5_proxy(downloader):
    """Launch must pass proxy={'server': 'socks5://172.21.0.4:9050'} for .onion."""
    clearnet_calls = []
    onion_calls = []

    def launch_factory(**kwargs):
        if "proxy" in kwargs:
            onion_calls.append(kwargs)
            return MagicMock(name="onion_browser")
        clearnet_calls.append(kwargs)
        return MagicMock(name="clearnet_browser")

    _pw, sync_playwright_mock = _patch_pw(launch_factory)

    with patch(
        "playwright.sync_api.sync_playwright",
        sync_playwright_mock,
    ):
        downloader._fetch_with_playwright(
            "http://kx5thpx2oluwml4w.onion/some/page"
        )

    assert len(onion_calls) == 1, f"onion launch not called: {onion_calls}"
    assert onion_calls[0]["proxy"]["server"] == "socks5://172.21.0.4:9050"
    assert len(clearnet_calls) == 0, f"clearnet launch leaked: {clearnet_calls}"


def test_clearnet_url_does_not_use_proxy(downloader):
    """Clearnet URLs must not get a proxy= arg."""
    clearnet_calls = []

    def launch_factory(**kwargs):
        clearnet_calls.append(kwargs)
        return MagicMock(name="clearnet_browser")

    _pw, sync_playwright_mock = _patch_pw(launch_factory)

    with patch(
        "playwright.sync_api.sync_playwright",
        sync_playwright_mock,
    ):
        downloader._fetch_with_playwright("https://example.com/")

    assert len(clearnet_calls) == 1
    assert "proxy" not in clearnet_calls[0]


def test_onion_and_clearnet_use_separate_browsers(downloader):
    """After fetching both, the downloader has two independent browsers."""
    launches = []

    def launch_factory(**kwargs):
        launches.append(kwargs)
        b = MagicMock(name=f"b{len(launches)}")
        b.new_page.return_value.goto.return_value = None
        b.new_page.return_value.content.return_value = "<html></html>"
        return b

    _pw, sync_playwright_mock = _patch_pw(launch_factory)

    with patch(
        "playwright.sync_api.sync_playwright",
        sync_playwright_mock,
    ):
        downloader._fetch_with_playwright("https://example.com/")
        downloader._fetch_with_playwright(
            "http://kx5thpx2oluwml4w.onion/"
        )

    assert len(launches) == 2
    assert "proxy" not in launches[0]
    assert launches[1]["proxy"]["server"] == "socks5://172.21.0.4:9050"


def test_close_releases_onion_browser(downloader):
    """close() must close the onion browser too."""
    def launch_factory(**kwargs):
        b = MagicMock(name="onion_browser")
        b.new_page.return_value.goto.return_value = None
        b.new_page.return_value.content.return_value = "<html></html>"
        return b

    _pw, sync_playwright_mock = _patch_pw(launch_factory)

    with patch(
        "playwright.sync_api.sync_playwright",
        sync_playwright_mock,
    ):
        downloader._fetch_with_playwright(
            "http://kx5thpx2oluwml4w.onion/"
        )
        onion_browser = downloader._onion_browser
        assert onion_browser is not None
        downloader.close()
        assert downloader._onion_browser is None
        onion_browser.close.assert_called()