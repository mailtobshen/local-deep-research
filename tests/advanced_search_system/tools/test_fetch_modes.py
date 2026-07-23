"""Unit tests for ``advanced_search_system.tools.fetch.build_fetch_tool``.

Pins the mode dispatch and the prompt-content contract for the two
summary variants (focus-only vs focus + overall query). Avoids any real
HTTP — patches ``ContentFetcher`` and the model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from local_deep_research.advanced_search_system.strategies.langgraph_agent_strategy import (
    SearchResultsCollector,
)
from local_deep_research.advanced_search_system.tools.fetch import (
    FETCH_MODES,
    build_fetch_tool,
)


def _fetcher_cm(*, status="success", title="Page", content="Body text"):
    fetcher = MagicMock()
    fetcher.fetch.return_value = {
        "status": status,
        "title": title,
        "content": content,
    }
    cm = MagicMock()
    cm.__enter__.return_value = fetcher
    cm.__exit__.return_value = False
    return cm


def _model_returning(text: str):
    """A model whose ``invoke`` returns an object with ``.content == text``."""
    msg = MagicMock()
    msg.content = text
    model = MagicMock()
    model.invoke.return_value = msg
    return model


def test_fetch_modes_constant_lists_all_supported_values():
    assert FETCH_MODES == (
        "disabled",
        "full",
        "summary_focus",
        "summary_focus_query",
    )


def test_disabled_mode_returns_none():
    assert build_fetch_tool("disabled", SearchResultsCollector([])) is None


def test_full_mode_returns_tool_with_url_only_signature():
    tool = build_fetch_tool("full", SearchResultsCollector([]))
    assert tool is not None
    assert "url" in tool.args
    assert "focus" not in tool.args


def test_summary_focus_requires_model():
    with pytest.raises(ValueError, match="summary_focus"):
        build_fetch_tool("summary_focus", SearchResultsCollector([]))


def test_summary_focus_query_requires_model():
    with pytest.raises(ValueError, match="summary_focus_query"):
        build_fetch_tool("summary_focus_query", SearchResultsCollector([]))


def test_summary_focus_tool_calls_model_with_focus_only_prompt():
    collector = SearchResultsCollector([])
    model = _model_returning("relevant fact 1")
    tool = build_fetch_tool(
        "summary_focus",
        collector,
        model=model,
        overall_query="should not appear",
    )
    assert tool is not None
    assert "focus" in tool.args

    cm = _fetcher_cm(title="T", content="page body")
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ):
        out = tool.invoke({"url": "http://example.com/", "focus": "year of X"})

    model.invoke.assert_called_once()
    prompt = model.invoke.call_args[0][0]
    # focus-only mode must NOT mention the overall query, even if one was passed
    assert "Why this page was fetched: year of X" in prompt
    assert "Overall research question" not in prompt
    # Output is prefixed with citation index so the agent can cite consistently
    assert out.startswith("[1] ")
    assert "relevant fact 1" in out


def test_summary_focus_query_includes_overall_query_in_prompt():
    collector = SearchResultsCollector([])
    model = _model_returning("relevant fact 2")
    tool = build_fetch_tool(
        "summary_focus_query",
        collector,
        model=model,
        overall_query="When did Liepmann receive the Prandtl-Ring Award?",
    )
    cm = _fetcher_cm(title="T", content="page body")
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ):
        tool.invoke({"url": "http://example.com/", "focus": "year"})

    prompt = model.invoke.call_args[0][0]
    assert (
        "Overall research question: When did Liepmann receive the Prandtl-Ring Award?"
        in prompt
    )
    assert "Why this page was fetched: year" in prompt


def test_summary_focus_query_with_empty_overall_query_falls_back_to_focus_only():
    """An empty overall_query should not produce a stale 'Overall research question:' line."""
    collector = SearchResultsCollector([])
    model = _model_returning("ok")
    tool = build_fetch_tool(
        "summary_focus_query",
        collector,
        model=model,
        overall_query="",  # empty
    )
    cm = _fetcher_cm()
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ):
        tool.invoke({"url": "http://example.com/", "focus": "x"})

    prompt = model.invoke.call_args[0][0]
    assert "Overall research question" not in prompt


def test_unknown_mode_raises_with_valid_modes_listed():
    with pytest.raises(ValueError, match="Unknown fetch mode"):
        build_fetch_tool("magic", SearchResultsCollector([]))


# ---- issue #3826: web.enable_javascript_rendering plumbing ----


def _captured_content_fetcher_kwargs(invoke_target):
    """Run *invoke_target* with ContentFetcher patched and return its
    constructor kwargs from the call.

    The patched factory returns a context manager whose ``fetch`` succeeds
    so the tool body runs end-to-end up to the call we care about.
    """
    cm = _fetcher_cm()
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ) as factory:
        invoke_target()
    assert factory.call_args is not None
    return factory.call_args.kwargs


def test_full_mode_passes_js_off_when_snapshot_disables_it():
    """Full-mode fetch tool must pass enable_js_rendering=False to
    ContentFetcher when the snapshot disables JS rendering."""
    collector = SearchResultsCollector([])
    snapshot = {
        "web.enable_javascript_rendering": {
            "value": False,
            "ui_element": "checkbox",
        }
    }
    tool = build_fetch_tool("full", collector, settings_snapshot=snapshot)
    kwargs = _captured_content_fetcher_kwargs(
        lambda: tool.invoke({"url": "http://example.com/"})
    )
    assert kwargs.get("enable_js_rendering") is False


def test_full_mode_passes_js_on_when_snapshot_enables_it():
    """When the snapshot opts in, JS rendering is forwarded to ContentFetcher."""
    collector = SearchResultsCollector([])
    snapshot = {
        "web.enable_javascript_rendering": {
            "value": True,
            "ui_element": "checkbox",
        }
    }
    tool = build_fetch_tool("full", collector, settings_snapshot=snapshot)
    kwargs = _captured_content_fetcher_kwargs(
        lambda: tool.invoke({"url": "http://example.com/"})
    )
    assert kwargs.get("enable_js_rendering") is True


def test_full_mode_defaults_to_js_off_without_snapshot():
    """No snapshot, no thread-local context → JS disabled (safe default)."""
    collector = SearchResultsCollector([])
    tool = build_fetch_tool("full", collector)
    kwargs = _captured_content_fetcher_kwargs(
        lambda: tool.invoke({"url": "http://example.com/"})
    )
    assert kwargs.get("enable_js_rendering") is False


def test_summary_mode_forwards_js_setting():
    """Summary-mode tool also forwards the JS toggle from the snapshot."""
    collector = SearchResultsCollector([])
    model = _model_returning("ok")
    snapshot = {
        "web.enable_javascript_rendering": {
            "value": False,
            "ui_element": "checkbox",
        }
    }
    tool = build_fetch_tool(
        "summary_focus",
        collector,
        model=model,
        settings_snapshot=snapshot,
    )
    kwargs = _captured_content_fetcher_kwargs(
        lambda: tool.invoke({"url": "http://example.com/", "focus": "x"})
    )
    assert kwargs.get("enable_js_rendering") is False


# ---- report.enable_images: fetch tool image extraction ----

_IMAGES_ON_SNAPSHOT = {
    "report.enable_images": {"value": True, "ui_element": "checkbox"}
}


def _fake_image(url="http://example.com/pic.jpg"):
    from local_deep_research.images.extractor import ExtractedImage

    return ExtractedImage(
        url=url,
        alt="pic",
        source_url="http://example.com/",
        source_title="T",
        width=800,
        height=600,
    )


def test_enable_images_off_does_not_call_image_pipeline():
    """Default (enable_images off): the image pipeline is never invoked and
    no html_content is attached — text-only path is unchanged."""
    collector = SearchResultsCollector([])
    tool = build_fetch_tool("full", collector)  # no snapshot → images off
    cm = _fetcher_cm(title="T", content="body")
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ), patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images"
    ) as fcwi:
        out = tool.invoke({"url": "http://example.com/"})

    fcwi.assert_not_called()
    assert "html_content" not in collector.results[0]
    assert out.startswith("[1] ")


def test_enable_images_on_attaches_html_content():
    """enable_images on: images are extracted and stored on the collector
    record as serialized html_content; the tool return text is unchanged."""
    collector = SearchResultsCollector([])
    tool = build_fetch_tool(
        "full", collector, settings_snapshot=_IMAGES_ON_SNAPSHOT
    )
    cm = _fetcher_cm(title="T", content="body")
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ), patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images",
        return_value={
            "http://example.com/": {"text": "body", "images": [_fake_image()]}
        },
    ) as fcwi:
        out = tool.invoke({"url": "http://example.com/"})

    fcwi.assert_called_once()
    html = collector.results[0].get("html_content")
    assert html is not None and "http://example.com/pic.jpg" in html
    # image JSON must NOT leak into the agent-facing tool output
    assert "html_content" not in out
    assert "pic.jpg" not in out
    assert out.startswith("[1] ")


def test_enable_images_pipeline_error_keeps_text_result():
    """If image extraction raises, the text fetch result is unaffected and no
    traceback is surfaced to the agent."""
    collector = SearchResultsCollector([])
    tool = build_fetch_tool(
        "full", collector, settings_snapshot=_IMAGES_ON_SNAPSHOT
    )
    cm = _fetcher_cm(title="T", content="body text")
    with patch(
        "local_deep_research.content_fetcher.ContentFetcher", return_value=cm
    ), patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images",
        side_effect=RuntimeError("boom"),
    ):
        out = tool.invoke({"url": "http://example.com/"})

    assert out.startswith("[1] ")
    assert "body text" in out
    assert "boom" not in out
    assert "html_content" not in collector.results[0]
