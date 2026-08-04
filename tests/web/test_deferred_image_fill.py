"""Tests for the deferred single-pass image fill.

The previous behaviour ran ``langgraph._ensure_images_for_results``
once per LLM reasoning round (22 rounds on the 2026-08-03 Shanghai
study, scraping ~80-200 pages per round). The new contract: the
image fetch is a single post-finalise pass done by
``research_service._deferred_image_fill`` after the markdown report
+ ``## Sources`` block are finalised, just before
``enhance_report_with_images`` runs.
"""

from unittest.mock import MagicMock, patch

import pytest

from local_deep_research.web.services import research_service
from local_deep_research.web.services.research_service import (
    _deferred_image_fill,
)


# ---------- helpers ---------------------------------------------------------


def _extracted_image(url="https://img/x.jpg", alt="Canton Tower",
                     source_url="https://src/page"):
    from local_deep_research.images.extractor import ExtractedImage

    return ExtractedImage(
        url=url,
        alt=alt,
        source_url=source_url,
        source_title="",
        width=800,
        height=600,
    )


def _make_results(*, cited_urls, fetched_with_html=None):
    """Build a results dict with one finding whose search_results
    contain the cited URLs (initially WITHOUT html_content unless
    ``fetched_with_html`` is supplied as a set)."""
    fetched_with_html = fetched_with_html or set()
    findings = [
        {
            "search_results": [
                {
                    "url": u,
                    "link": u,
                    "title": f"Title {i}",
                    "html_content": "pre-existing" if u in fetched_with_html else "",
                }
                for i, u in enumerate(cited_urls)
            ]
        }
    ]
    return {"findings": findings}


# ---------- _deferred_image_fill: end-to-end ------------------------------


class TestDeferredImageFillEndToEnd:
    def test_fills_cited_urls_and_attaches_html_content(self):
        """One pass per URL, JSON-serialised into
        search_results[].html_content so postprocessing can find it."""
        results = _make_results(
            cited_urls=["https://src/a", "https://src/b", "https://src/c"]
        )
        final_md = (
            "## Canton Tower\n\nThe tower [1] is tall.\n\n"
            "## 参考文献\n\n"
            "[1] Source\n   URL: https://src/a\n"
            "[2] Source\n   URL: https://src/b\n"
            "[3] Source\n   URL: https://src/c\n"
        )
        settings = {"report.enable_images": True}

        # fetch_content_with_images returns a dict keyed by URL with
        # an ``images`` list per URL.
        data = {
            "https://src/a": {"text": "t1", "images": [_extracted_image(
                url="https://img/a.jpg", alt="A", source_url="https://src/a"
            )]},
            "https://src/b": {"text": "t2", "images": [_extracted_image(
                url="https://img/b.jpg", alt="B", source_url="https://src/b"
            )]},
            "https://src/c": {"text": "t3", "images": []},  # no images
        }

        with patch.object(
            research_service, "_parse_sources_markdown_urls", create=True
        ):
            with patch(
                "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images",
                return_value=data,
            ):
                filled = _deferred_image_fill(
                    "r",
                    final_markdown=final_md,
                    results=results,
                    settings_snapshot=settings,
                )

        assert filled == 2, "only URLs with >= 1 image should count"
        # The two URLs that returned images now have populated
        # ``html_content`` (serialised JSON, non-empty).
        htmls = [
            sr["html_content"]
            for sr in results["findings"][0]["search_results"]
        ]
        non_empty = [h for h in htmls if h]
        assert len(non_empty) == 2
        # The no-image URL's html_content stays empty.
        assert htmls[2] == ""

    def test_skips_when_enable_images_false(self):
        results = _make_results(cited_urls=["https://src/a"])
        final_md = (
            "## X\n\nY [1].\n\n## 参考文献\n\n[1] S\n   URL: https://src/a\n"
        )
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images"
        ) as fetch_mock:
            filled = _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": False},
            )
        assert filled == 0
        # Crucially: NO fetch was made when images are off.
        fetch_mock.assert_not_called()

    def test_skips_when_no_cited_urls(self):
        """A report that cites no URLs is a no-op."""
        results = _make_results(cited_urls=[])
        final_md = "## X\n\nSome prose without any citations."
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images"
        ) as fetch_mock:
            filled = _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": True},
            )
        assert filled == 0
        fetch_mock.assert_not_called()

    def test_idempotent_against_existing_html_content(self):
        """URLs whose html_content is already populated (e.g. by an
        earlier text fetch) MUST NOT be re-fetched — the deferred
        pass is purely additive."""
        results = _make_results(
            cited_urls=["https://src/a", "https://src/b"],
            fetched_with_html={"https://src/a"},
        )
        final_md = (
            "## X\n\nY [1] [2].\n\n## 参考文献\n\n"
            "[1] S\n   URL: https://src/a\n"
            "[2] S\n   URL: https://src/b\n"
        )
        data = {
            "https://src/b": {"text": "t", "images": [_extracted_image(
                url="https://img/b.jpg", alt="B", source_url="https://src/b"
            )]},
        }
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images",
            return_value=data,
        ) as fetch_mock:
            filled = _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": True},
            )
        # Only ``b`` was fetched; ``a`` was skipped because its
        # html_content was already populated.
        assert filled == 1
        call = fetch_mock.call_args
        assert call.args[0] == ["https://src/b"], (
            f"expected only https://src/b to be fetched, got {call.args[0]}"
        )
        # ``a``'s html_content is unchanged.
        a_sr = next(
            sr for sr in results["findings"][0]["search_results"]
            if (sr.get("url") or sr.get("link")) == "https://src/a"
        )
        assert a_sr["html_content"] == "pre-existing"

    def test_fetch_failure_does_not_break_research(self):
        """Network failure during the deferred pass is downgraded to
        a debug log; the report is still served (text-only)."""
        results = _make_results(cited_urls=["https://src/a"])
        final_md = (
            "## X\n\nY [1].\n\n## 参考文献\n\n[1] S\n   URL: https://src/a\n"
        )
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images",
            side_effect=Exception("network down"),
        ):
            filled = _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": True},
            )
        assert filled == 0
        # The result is still served (results dict unchanged).
        a_sr = next(
            sr for sr in results["findings"][0]["search_results"]
        )
        assert a_sr["html_content"] == ""


# ---------- langgraph._finalize no longer fetches per round ----------------


class TestLanggraphFinalizeDoesNotFetchPerRound:
    """The previous contract had ``_finalize`` call
    ``_ensure_images_for_results`` once per LLM reasoning round.
    That was the cause of the 7-hour Playwright rendering time
    on the 2026-08-03 Shanghai run. The new contract: the
    langgraph loop fetches text only; the image fill is a single
    post-loop pass done by ``_deferred_image_fill``.

    The behavioural test for this lives below as a focused
    integration test that exercises the real ``_finalize`` body
    via a stubbed agent. As a cheap regression guard, we ALSO
    assert the source code does not call
    ``_ensure_images_for_results`` from inside ``_finalize`` —
    so a future engineer cannot accidentally re-introduce the
    per-round fetch without a deliberate test removal.
    """

    def test_finalize_does_not_call_ensure_images_for_results(
        self, loguru_caplog
    ):
        """Static regression guard: ``_finalize`` no longer calls
        ``self._ensure_images_for_results``. The runtime test
        below exercises the actual code path. We look for the
        *call* form ``self._ensure_images_for_results(`` rather
        than any mention, because the docstring intentionally
        names the removed method for the benefit of future
        maintainers."""
        import inspect
        import re

        from local_deep_research.advanced_search_system.strategies import (
            langgraph_agent_strategy,
        )

        src = inspect.getsource(
            langgraph_agent_strategy.LangGraphAgentStrategy._finalize
        )
        # Strip the docstring (the triple-quoted block at the top of
        # the function body) so the test does not false-positive
        # on the docstring's mention of the removed method.
        body = re.sub(r'"""[\s\S]*?"""', "", src, count=1)
        # The call form is the regex below — covers both
        # ``self._ensure_images_for_results(...)`` and
        # ``_ensure_images_for_results(...)``.
        assert not re.search(
            r"\bself\._ensure_images_for_results\s*\(", body
        ) and not re.search(
            r"^[^.]\b_ensure_images_for_results\s*\(", body, re.MULTILINE
        ), (
            "_finalize must not call _ensure_images_for_results; "
            "image fetch is deferred to research_service._deferred_image_fill"
        )
        # And the audit event that replaced it is present.
        assert "deferred to post-finalize pass" in src

    def test_finalize_runtime_no_per_round_fetch(
        self, loguru_caplog
    ):
        """End-to-end smoke: invoke the langgraph strategy
        ``analyze_topic`` with a stubbed agent that emits a
        final_content, and assert ``_ensure_images_for_results``
        is NEVER called (the new contract defers to
        ``_deferred_image_fill``)."""
        from local_deep_research.advanced_search_system.strategies import (
            langgraph_agent_strategy,
        )
        from langchain_core.messages import AIMessage

        final_msg = AIMessage(content="OK final answer with [1].")

        class _OneChunkAgent:
            def stream(self, *a, **kw):
                return iter([{"agent": {"messages": [final_msg]}}])

        with patch(
            "langchain.agents.create_agent",
            return_value=_OneChunkAgent(),
        ):
            strat = langgraph_agent_strategy.LangGraphAgentStrategy.__new__(
                langgraph_agent_strategy.LangGraphAgentStrategy
            )
            strat.model = MagicMock()
            strat.search = MagicMock()
            strat.collector = MagicMock()
            strat.collector.results = [
                {
                    "url": "https://src/page",
                    "link": "https://src/page",
                    "title": "Page",
                    "snippet": "snippet",
                    "html_content": "cached",
                }
            ]
            strat.collector.all_links = strat.collector.results
            strat.collector.attach_html_content = MagicMock(
                return_value=True
            )
            strat._ensure_images_for_results = MagicMock(
                wraps=strat._ensure_images_for_results
            )
            strat.citation_handler = MagicMock()
            strat.citation_handler.analyze_followup = MagicMock(
                return_value={
                    "content": "OK final answer with [1].",
                    "documents": [],
                }
            )
            strat._format_agent_error = MagicMock(
                return_value="agent error"
            )
            strat.progress_callback = None
            strat.fetch_mode = "summary_focus"
            strat.programmatic_mode = False
            strat.include_sub_research = False
            strat.check_termination = MagicMock()
            strat._update_progress = MagicMock()
            strat._synthesize_from_collector = MagicMock(
                return_value="synthesised"
            )
            strat._search_engine_name = "auto"
            strat._get_current_engine_name = MagicMock(
                return_value="searxng"
            )
            strat.all_links_of_system = []
            strat.collector.reset = MagicMock()
            strat.titles = {}
            strat.settings_snapshot = {
                "search.engine.web.retriever": "searxng",
            }
            strat._build_tools = MagicMock(
                return_value=[MagicMock(name="placeholder_tool")]
            )
            strat.max_iterations = 10
            strat.max_sub_iterations = 4
            strat.titles = {}

            strat._ensure_images_for_results.reset_mock()
            result = strat.analyze_topic("上海 旅游")
            text = "\n".join(
                r.getMessage() for r in loguru_caplog.records
            )
            assert "deferred to post-finalize pass" in text
            # Critical regression guard: per-round fetch is gone.
            strat._ensure_images_for_results.assert_not_called()
            assert result["current_knowledge"].startswith("OK final")
