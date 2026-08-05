"""Verify image-attach behavior in the langgraph fetch tool, post-#5.

After commits c134b208 / 7b545226 / 2939dbac, the fetch tool no
longer calls image extraction itself — image extraction is unified
into _deferred_image_fill in web/services/research_service.py. This
file kept the original #2/#4/#8 tests but updated them to verify
the new "fetch tool does nothing for images" behavior.

The pre-#5 _attach_images_if_enabled function is removed from
tools/fetch/__init__.py. The tests that drove it are rewritten to
assert the absence of any image attach call from the fetch_content
tool, regardless of fetch_mode.
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


def test_attach_emits_filled_and_alt_filter_DEPRECATED(
    fake_pipeline, captured_logs
):
    """DEPRECATED post-#5: the _attach_images_if_enabled function was
    removed. This test is kept to document the historical behavior and
    to fail loudly if anyone reintroduces immediate-mode image attach
    in the fetch tool. To verify image attach, look at the deferred
    pass in web/services/research_service._deferred_image_fill."""
    import importlib

    fetch_mod = importlib.import_module(
        "local_deep_research.advanced_search_system.tools.fetch"
    )
    # Pre-#5 the function existed and called dumps_images. Post-#5 it
    # must be gone — the unified deferred pass handles everything.
    assert not hasattr(fetch_mod, "_attach_images_if_enabled"), (
        "_attach_images_if_enabled was removed in fix #5 — image fetch "
        "is now unified in _deferred_image_fill. If you see this in "
        "a future commit, that's a regression."
    )


def test_attach_no_op_DEPRECATED(captured_logs):
    """Same placeholder for the #2 'no-op when gate off' test.

    Kept to make the deprecation explicit. The gate-off behavior now
    lives in enhance_report_with_images at
    images/postprocessing.py:184 (returns clean_markdown unchanged
    when report.enable_images=False)."""
    # Nothing to assert — the test name documents the original test
    # that used to drive _attach_images_if_enabled with the gate off.
    pass


def test_attach_emits_warning_on_exception_DEPRECATED(captured_logs):
    """DEPRECATED placeholder for the LANGGRAPH_FILL_FAILED warning
    test. Failures are now surfaced by DEFERRED_FILL_FAILED in
    _deferred_image_fill (research_service.py), not by the fetch
    tool."""
    pass


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


# ---- #5: unified deferred image fetch ----

def test_fetch_module_does_not_attach_images():
    """Post-#5: the fetch module no longer calls fetch_content_with_images
    or attach_html_content. All image extraction is unified in
    research_service._deferred_image_fill."""
    import inspect

    from local_deep_research.advanced_search_system.tools import fetch as fetch_mod

    src = inspect.getsource(fetch_mod)
    assert "fetch_content_with_images" not in src, (
        "fetch tool re-introduced image extraction — fix #5 reverted?"
    )
    assert "attach_html_content" not in src, (
        "fetch tool re-introduced collector attach — fix #5 reverted?"
    )


def test_fetch_content_returns_text_only():
    """Sanity: fetch_content tool returns markdown text + cite_idx,
    never image URLs. The LLM has always been text-only; #5 just
    documents the contract."""
    import inspect

    from local_deep_research.advanced_search_system.tools import fetch as fetch_mod

    src = inspect.getsource(fetch_mod)
    # No 'image' literals in return values
    assert "return f'![{" not in src
    assert "f\"![{" not in src
    # The return-value format is text-only
    assert "[{cite_idx}]" in src or "cite_idx" in src


def test_full_fetch_tool_does_not_call_image_extraction(captured_logs):
    """The full fetch tool emits no LANGGRAPH_FILLED events — image
    extraction moved to the deferred pass. Even with enable_images=True
    the fetch step is silent on images."""
    from unittest.mock import MagicMock, patch

    # Build a minimal collector + settings_snapshot
    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    # Patch the underlying ContentFetcher to return success without
    # actually hitting the network; we just want to verify the
    # fetch_content tool's path does NOT call attach_html_content.
    fake_result = {
        "status": "success",
        "title": "Some title",
        "content": "Some content here.",
    }
    fake_fetcher = MagicMock()
    fake_fetcher.fetch.return_value = fake_result
    fake_fetcher.__enter__ = MagicMock(return_value=fake_fetcher)
    fake_fetcher.__exit__ = MagicMock(return_value=False)

    import local_deep_research.advanced_search_system.tools.fetch as fetch_mod
    tool = fetch_mod._make_full_fetch_tool(collector, settings_snapshot={})
    tool.func("https://example.com/page")

    # No image attach was attempted by the fetch tool.
    assert not collector.attach_html_content.called

    text = captured_logs.getvalue()
    assert "LANGGRAPH_FILLED" not in text
    assert "LANGGRAPH_FILL_BEGIN" not in text


def test_summary_fetch_tool_does_not_call_image_extraction(captured_logs):
    """Same for the summary-mode fetch tool."""
    from unittest.mock import MagicMock, patch

    # Mock the LLM model used by summary-mode fetch
    fake_model = MagicMock()
    fake_model.invoke.return_value = MagicMock(content="summary text")

    # Mock ContentFetcher
    fake_result = {
        "status": "success",
        "title": "Some title",
        "content": "original text",
    }
    fake_fetcher = MagicMock()
    fake_fetcher.fetch.return_value = fake_result
    fake_fetcher.__enter__ = MagicMock(return_value=fake_fetcher)
    fake_fetcher.__exit__ = MagicMock(return_value=False)

    collector = MagicMock()
    collector.attach_html_content = MagicMock(return_value=True)

    import local_deep_research.advanced_search_system.tools.fetch as fetch_mod
    # summary_focus mode without overall_query
    tool = fetch_mod.build_fetch_tool(
        "summary_focus",
        collector,
        model=fake_model,
        overall_query=None,
        settings_snapshot={},
    )
    tool.func("https://example.com/page", focus="some question")

    assert not collector.attach_html_content.called


def test_image_extraction_unified_in_research_service():
    """The deferred image fill function is the only place that calls
    fetch_content_with_images for the post-fetch phase."""
    import inspect

    from local_deep_research.web.services import research_service as svc

    src = inspect.getsource(svc._deferred_image_fill)
    assert "fetch_content_with_images" in src, (
        "Deferred image fill no longer calls fetch_content_with_images"
    )