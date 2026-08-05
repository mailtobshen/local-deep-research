"""Verify _attach_images_if_enabled emits the new IMG-TRACE events from
commits c134b208 and 7b545226 (#2, #4 in
docs/superpowers/plans/2026-08-05-image-chain-9-fixes.md).

The function lives behind an in-function import of
``thread_settings.get_bool_setting_from_snapshot`` and
``pipeline.fetch_content_with_images``; we patch both via the module
attributes that those in-function imports resolve to.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from local_deep_research.images.extractor import ExtractedImage


def _make_img(url: str, alt: str) -> ExtractedImage:
    return ExtractedImage(
        url=url,
        alt=alt,
        source_url="",
        source_title="",
        width=0,
        height=0,
    )


@pytest.fixture
def fake_pipeline():
    """Patch ``fetch_content_with_images`` for the duration of the test."""
    imgs = [
        _make_img("https://a/1.jpg", "有内容"),
        _make_img("https://a/2.jpg", ""),
        _make_img("https://a/3.jpg", ""),
        _make_img("https://a/4.jpg", "   "),
        _make_img("https://a/5.jpg", "第二张有alt"),
    ]

    def _stub(urls, **kw):
        return {urls[0]: {"images": imgs}}

    import local_deep_research.research_library.downloaders.extraction.pipeline as p

    pmp = patch.object(p, "fetch_content_with_images", side_effect=_stub)
    pmp.start()
    try:
        yield _stub
    finally:
        pmp.stop()


@pytest.fixture
def captured_logs():
    """Capture loguru output via stdlib StringIO buffer.

    ``local_deep_research.__init__`` calls ``logger.disable("local_deep_research")``
    to silence library logs by default. Tests must re-enable the namespace
    to see IMG-TRACE events emitted from inside ``_attach_images_if_enabled``
    (which lives in ``local_deep_research.advanced_search_system.tools.fetch``).
    """
    import io

    from loguru import logger

    buf = io.StringIO()
    sink_id = logger.add(buf, level="DEBUG", format="{message}")
    logger.enable("local_deep_research")
    try:
        yield buf
    finally:
        logger.disable("local_deep_research")
        logger.remove(sink_id)


def test_attach_emits_filled_and_alt_filter(
    fake_pipeline, captured_logs
):
    """5 imgs (3 alt-empty) → LANGGRAPH_FILLED images=5 + alt_filter dropped=3."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod

    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True):
        _attach_images_if_enabled(
            collector,
            "https://example.com/page",
            "title",
            {},
            enable_js_rendering=False,
        )

    text = captured_logs.getvalue()

    # IMG-TRACE schema is uniform across stages (per memory note
    # img-trace-five-key-schema): src_url is mandatory.
    assert "[IMG-TRACE] LANGGRAPH_FILLED src_url=https://example.com/page images=5" in text, (
        f"LANGGRAPH_FILLED missing or wrong image count. Got:\n{text}"
    )
    assert "[IMG-TRACE] LANGGRAPH_FILL alt_filter dropped=3" in text, (
        f"alt_filter log missing. Got:\n{text}"
    )
    assert "[IMG-TRACE] LANGGRAPH_FILL_BEGIN" in text

    # attach_html_content was called with the filtered payload (2 imgs only,
    # not the original 5).
    assert collector.attach_html_content.called
    payload = collector.attach_html_content.call_args[0][1]
    assert '"https://a/1.jpg"' in payload
    assert '"https://a/5.jpg"' in payload
    assert '"https://a/2.jpg"' not in payload
    assert '"https://a/3.jpg"' not in payload
    assert '"https://a/4.jpg"' not in payload


def test_attach_no_op_when_enable_images_off(captured_logs):
    """When settings gate fails, no fetch happens and no IMG-TRACE fires."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod
    import local_deep_research.research_library.downloaders.extraction.pipeline as p

    fetch_called = []
    def _stub(urls, **kw):
        fetch_called.append(urls)
        return {}

    collector = MagicMock()
    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=False), \
         patch.object(p, "fetch_content_with_images", side_effect=_stub):
        _attach_images_if_enabled(collector, "https://x", "t", {}, False)

    assert not fetch_called
    assert not collector.attach_html_content.called
    text = captured_logs.getvalue()
    assert "LANGGRAPH_FILLED" not in text


def test_attach_emits_warning_on_exception(captured_logs):
    """Exception during fetch → LANGGRAPH_FILL_FAILED warning, not silent."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod
    import local_deep_research.research_library.downloaders.extraction.pipeline as p

    def _boom(urls, **kw):
        raise RuntimeError("playwright offline")

    collector = MagicMock()
    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True), \
         patch.object(p, "fetch_content_with_images", side_effect=_boom):
        _attach_images_if_enabled(collector, "https://x", "t", {}, False)

    text = captured_logs.getvalue()
    assert "LANGGRAPH_FILL_FAILED url=https://x reason=RuntimeError: playwright offline" in text, (
        f"FAILED log missing. Got:\n{text}"
    )
    assert not collector.attach_html_content.called


# ---- #7: dumps_images / loads_images silent failure logging ----

def test_loads_warns_on_bad_json(captured_logs):
    """Bad JSON should emit LOADS_FAIL IMG-TRACE warning, not be silent."""
    from local_deep_research.images.serialize import loads_images

    out = loads_images("not json {[")
    assert out == []
    text = captured_logs.getvalue()
    assert "[IMG-TRACE] LOADS_FAIL" in text


def test_loads_warns_on_non_list(captured_logs):
    """JSON object (not array) should also warn."""
    from local_deep_research.images.serialize import loads_images

    out = loads_images('{"url": "x"}')
    assert out == []
    text = captured_logs.getvalue()
    assert "[IMG-TRACE] LOADS_FAIL reason=not_list" in text


def test_dumps_warns_on_non_serializable(captured_logs):
    """A non-serializable object inside dumps_images should emit DUMPS_FAIL."""
    from local_deep_research.images.serialize import dumps_images

    class Bad:
        def __repr__(self):
            return "Bad()"
        # No JSON-serializable representation

    class BadImg:
        url = "https://x"
        alt = "ok"
        source_url = ""
        source_title = ""
        width = Bad()  # width is not JSON-serializable
        height = 0

    out = dumps_images([BadImg()])
    assert out == "[]"
    text = captured_logs.getvalue()
    assert "[IMG-TRACE] DUMPS_FAIL" in text


# ---- #8: fetch mode split ----

def test_summary_focus_query_skips_image_attach(captured_logs):
    """In summary_focus_query mode the tool returns LLM-rewritten
    summary, not original page HTML. Image attach would store the wrong
    content; #8 makes it a no-op and the deferred pass picks it up."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod

    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True):
        _attach_images_if_enabled(
            collector,
            "https://example.com/summary",
            "title",
            {},
            enable_js_rendering=False,
            fetch_mode="summary_focus_query",
        )

    # No image attach attempted.
    assert not collector.attach_html_content.called

    text = captured_logs.getvalue()
    assert "[IMG-TRACE] LANGGRAPH_FILL_SKIP url=https://example.com/summary" in text
    assert "fetch_mode=summary_focus_query" in text
    assert "reason=summary_mode" in text
    # Crucially, the FILLED event should NOT fire for summary modes.
    assert "LANGGRAPH_FILLED" not in text


def test_summary_focus_mode_also_skips(captured_logs):
    """Both summary modes (with and without overall_query) skip."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod

    collector = MagicMock()
    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True):
        _attach_images_if_enabled(
            collector,
            "https://x/y",
            "t",
            {},
            False,
            fetch_mode="summary_focus",
        )
    assert not collector.attach_html_content.called
    assert "fetch_mode=summary_focus" in captured_logs.getvalue()


def test_full_mode_still_attaches_images(captured_logs):
    """Pre-#8 behavior preserved: 'full' mode (returns original HTML)
    still attaches images. Same code path tested by the #2/#4 tests
    but explicitly verified here with fetch_mode='full' param."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod
    import local_deep_research.research_library.downloaders.extraction.pipeline as p
    from local_deep_research.images.extractor import ExtractedImage

    imgs = [
        ExtractedImage(url="https://x/1.jpg", alt="ok", source_url="", source_title="", width=0, height=0),
    ]

    def fake_fetch(urls, **kw):
        return {urls[0]: {"images": imgs}}

    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True), \
         patch.object(p, "fetch_content_with_images", side_effect=fake_fetch):
        _attach_images_if_enabled(
            collector,
            "https://x/full-page",
            "title",
            {},
            False,
            fetch_mode="full",
        )

    assert collector.attach_html_content.called
    text = captured_logs.getvalue()
    assert "[IMG-TRACE] LANGGRAPH_FILLED src_url=https://x/full-page images=1" in text
    assert "LANGGRAPH_FILL_SKIP" not in text


def test_summary_mode_settings_gate_off_still_skips_silently(captured_logs):
    """When enable_images is OFF, summary mode path is unchanged
    (no LANGGRAPH_FILL_SKIP event emitted — gate takes priority)."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod

    collector = MagicMock()
    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=False):
        _attach_images_if_enabled(
            collector,
            "https://x",
            "t",
            {},
            False,
            fetch_mode="summary_focus_query",
        )
    assert not collector.attach_html_content.called
    text = captured_logs.getvalue()
    # Gate-off path emits nothing; the SKIP event is only for
    # enable_images=True but summary mode.
    assert "LANGGRAPH_FILL_SKIP" not in text


def test_default_fetch_mode_is_full_for_backcompat(captured_logs):
    """No fetch_mode kwarg → defaults to 'full' → attaches images
    (preserves pre-#8 call sites that didn't pass the param)."""
    from local_deep_research.advanced_search_system.tools.fetch import (
        _attach_images_if_enabled,
    )
    import local_deep_research.config.thread_settings as ts_mod
    import local_deep_research.research_library.downloaders.extraction.pipeline as p
    from local_deep_research.images.extractor import ExtractedImage

    imgs = [
        ExtractedImage(url="https://x/1.jpg", alt="ok", source_url="", source_title="", width=0, height=0),
    ]

    def fake_fetch(urls, **kw):
        return {urls[0]: {"images": imgs}}

    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    with patch.object(ts_mod, "get_bool_setting_from_snapshot", return_value=True), \
         patch.object(p, "fetch_content_with_images", side_effect=fake_fetch):
        # NOTE: no fetch_mode kwarg — relies on default
        _attach_images_if_enabled(
            collector, "https://x/default", "t", {}, False,
        )

    assert collector.attach_html_content.called
    assert "LANGGRAPH_FILL_SKIP" not in captured_logs.getvalue()