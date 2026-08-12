"""Tests for the deferred single-pass image fill.

The previous behaviour ran ``langgraph._ensure_images_for_results``
once per LLM reasoning round (22 rounds on the 2026-08-03 Shanghai
study, scraping ~80-200 pages per round). The new contract: the
image fetch is a single post-finalise pass done by
``research_service._deferred_image_fill`` after the markdown report
+ ``## Sources`` block are finalised, just before
``enhance_report_with_images`` runs.
"""

import logging
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

    def test_deferred_fetched_img_event_carries_four_user_fields(
        self, loguru_caplog
    ):
        """User requirement (re-raised 2026-08-04): every
        ``DEFERRED_FETCHED_IMG`` line MUST carry the four fields
        the user asked for — citation number, reference URL, image
        source URL, and image alt. Without them, a log consumer
        cannot tell which fetched image is associated with which
        in-text citation.
        """
        results = _make_results(
            cited_urls=["https://src/canton-tower", "https://src/lujiazui"]
        )
        final_md = (
            "## Canton Tower\n\nThe tower [1] is tall.\n\n"
            "## Lujiazui\n\nThe skyline [2] is iconic.\n\n"
            "## 参考文献\n\n"
            "[1] Tower source\n   URL: https://src/canton-tower\n"
            "[2] Skyline source\n   URL: https://src/lujiazui\n"
        )
        data = {
            "https://src/canton-tower": {
                "text": "tower page",
                "images": [
                    _extracted_image(
                        url="https://img/tower.jpg",
                        alt="Canton Tower at night",
                        source_url="https://src/canton-tower",
                    )
                ],
            },
            "https://src/lujiazui": {
                "text": "skyline page",
                "images": [
                    _extracted_image(
                        url="https://img/skyline.jpg",
                        alt="Lujiazui skyline",
                        source_url="https://src/lujiazui",
                    )
                ],
            },
        }
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images",
            return_value=data,
        ):
            _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": True},
            )
        text = "\n".join(r.getMessage() for r in loguru_caplog.records)
        # One DEFERRED_FETCHED_IMG per extracted image — 2 total.
        per_image = [
            line for line in text.splitlines()
            if "[IMG-TRACE] DEFERRED_FETCHED_IMG" in line
        ]
        assert len(per_image) == 2
        for line in per_image:
            # The four user-required fields MUST be present and
            # well-formed.
            for key in ("img_alt", "img_url", "img_source_url",
                        "cite_num", "ref_url"):
                assert f"{key}=" in line, (
                    f"DEFERRED_FETCHED_IMG missing {key}=: {line!r}"
                )
        # Spot-check: tower image carries cite_num=1, lujiazui=2.
        tower = next(line for line in per_image if "tower.jpg" in line)
        lujiazui = next(line for line in per_image if "skyline.jpg" in line)
        assert "cite_num=1" in tower
        assert "ref_url=https://src/canton-tower" in tower
        assert "img_source_url=https://src/canton-tower" in tower
        assert "'Canton Tower at night'" in tower
        assert "cite_num=2" in lujiazui
        assert "ref_url=https://src/lujiazui" in lujiazui
        assert "img_source_url=https://src/lujiazui" in lujiazui
        assert "'Lujiazui skyline'" in lujiazui

    def test_deferred_filled_summary_includes_cite_num_and_ref_url(
        self, loguru_caplog
    ):
        """The per-URL ``DEFERRED_FILLED`` summary event must
        carry the full four-field vocabulary the user asked for
        (cite_num, ref_url, img_source_url, img_alt) so a single
        line tells you the citation number, the reference URL,
        the page the images came from, and the alt text of every
        image that was attached."""
        results = _make_results(cited_urls=["https://src/a"])
        final_md = (
            "## X\n\nY [1].\n\n## 参考文献\n\n[1] S\n   URL: https://src/a\n"
        )
        data = {
            "https://src/a": {
                "text": "t",
                "images": [
                    _extracted_image(
                        url="https://img/a.jpg",
                        alt="A",
                        source_url="https://src/a",
                    ),
                    _extracted_image(
                        url="https://img/b.jpg",
                        alt="B",
                        source_url="https://src/a",
                    ),
                ],
            }
        }
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images",
            return_value=data,
        ):
            _deferred_image_fill(
                "r",
                final_markdown=final_md,
                results=results,
                settings_snapshot={"report.enable_images": True},
            )
        text = "\n".join(r.getMessage() for r in loguru_caplog.records)
        summary = [
            line for line in text.splitlines()
            if "[IMG-TRACE] DEFERRED_FILLED" in line
        ]
        assert len(summary) == 1
        for key in ("cite_num", "ref_url", "img_source_url", "img_alt"):
            assert f"{key}=" in summary[0], (
                f"DEFERRED_FILLED missing {key}=: {summary[0]!r}"
            )
        assert "cite_num=1" in summary[0]
        assert "ref_url=https://src/a" in summary[0]
        assert "img_source_url=https://src/a" in summary[0]
        # The alts of the two images appear in the bracketed list.
        assert "'A'" in summary[0]
        assert "'B'" in summary[0]
        assert "img_alt_count=2" in summary[0]


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


# ---------- post-reset shape: cited URL survives ONLY in
#             ``all_links_of_system`` ---------------------------------------


class TestDeferredFillPostResetShape:
    """Detailed mode: ``collector.reset()`` clears ``_results`` between
    subsections, so a cross-subsection cited URL survives ONLY in
    ``all_links_of_system``. The attach loop must write there too, or
    ``filled`` stays 0 and ``build_citation_index`` sees an empty map.

    Regression for research a6e77742 (2026-08-07): 2919 images fetched,
    filled=0/77, BANK_EMPTY reason=no_citations_or_html.
    """

    CITED = "https://www.chinadiscovery.com/shanghai/zhujiajiao.html"

    def _markdown(self):
        return (
            "## 朱家角古镇\n\n"
            f"江南水乡 [[56]]({self.CITED})。\n\n"
            "## Sources\n\n"
            "[56, 1224] Zhujiajiao Ancient Town\n"
            f"   URL: {self.CITED}\n"
        )

    def _results_post_reset(self):
        """findings[] holds only the LAST subsection (a different URL);
        the cited URL lives in the cumulative all_links_of_system list."""
        return {
            "findings": [
                {"search_results": [{"link": "https://other.example/last"}]}
            ],
            "all_links_of_system": [{"link": self.CITED}],
        }

    def test_attaches_when_url_only_in_all_links_of_system(self):
        results = self._results_post_reset()
        fetched = {self.CITED: {"text": "t", "images": [_extracted_image(
            url="https://img/z.jpg", alt="放生桥", source_url=self.CITED)]}}

        with patch(
            "local_deep_research.research_library.downloaders.extraction."
            "pipeline.fetch_content_with_images",
            return_value=fetched,
        ):
            filled = _deferred_image_fill(
                "res-post-reset",
                final_markdown=self._markdown(),
                results=results,
                settings_snapshot={"report.enable_images": True},
            )

        assert filled == 1, (
            "attach loop must match the cited URL inside "
            "all_links_of_system, not only findings[].search_results[]"
        )
        record = results["all_links_of_system"][0]
        assert record.get("html_content"), (
            "html_content must be written onto the all_links_of_system record"
        )

    def test_all_links_html_visible_to_build_citation_index(self):
        """End-to-end read check: what the fill writes, the index must see."""
        from local_deep_research.images.relevance import build_citation_index

        results = self._results_post_reset()
        fetched = {self.CITED: {"text": "t", "images": [_extracted_image(
            url="https://img/z.jpg", alt="放生桥", source_url=self.CITED)]}}

        with patch(
            "local_deep_research.research_library.downloaders.extraction."
            "pipeline.fetch_content_with_images",
            return_value=fetched,
        ):
            _deferred_image_fill(
                "res-post-reset-2",
                final_markdown=self._markdown(),
                results=results,
                settings_snapshot={"report.enable_images": True},
            )

        num_to_url, _sections, url_to_html = build_citation_index(
            self._markdown(), results
        )
        assert num_to_url, "citation index should parse the Sources block"
        assert url_to_html, (
            "url_to_html must be non-empty — an empty map is exactly the "
            "BANK_EMPTY reason=no_citations_or_html production signature"
        )
        assert self.CITED in url_to_html


# ---------- call-site contract: same dict to fill and postprocessing -------


def test_inject_returns_new_dict_original_lacks_key():
    """Guards the call-site contract.

    ``_inject_all_links_of_system`` returns a NEW dict; the original
    ``results`` never gains ``all_links_of_system``. So passing
    ``results_for_fill`` to the fill but the original ``results`` to
    ``enhance_report_with_images`` hides everything the fill wrote
    into the cumulative list. Both stages must receive the same dict.
    """
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    class _Sys:
        all_links_of_system = [{"link": "https://cited.example/page"}]

    results = {"findings": []}
    merged = _inject_all_links_of_system(results, _Sys())

    assert "all_links_of_system" in merged
    assert "all_links_of_system" not in results, (
        "original results must NOT gain the key — this is precisely why "
        "both stages have to be handed `merged`, not `results`"
    )
    assert merged["all_links_of_system"][0] is _Sys.all_links_of_system[0], (
        "record objects are shared, so writes through `merged` are visible "
        "to any holder of the same record"
    )


# ---------- observability: ATTACH_MISS -------------------------------------


def test_attach_miss_event_emitted_when_no_record_matches(loguru_caplog):
    """A cited URL matching no record must announce itself.

    Silence here is what made research a6e77742 cost a full forensic
    pass: filled=0/77 with no per-URL reason.
    """
    cited = "https://orphan.example/never-in-results"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7, 1] Orphan\n"
        f"   URL: {cited}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/o.jpg", alt="orphan", source_url=cited)]}}

    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images",
        return_value=fetched,
    ):
        filled = _deferred_image_fill(
            "res-miss",
            final_markdown=markdown,
            results=results,
            settings_snapshot={"report.enable_images": True},
        )

    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 0
    assert "ATTACH_MISS" in text
    assert cited in text


# ---------- structural no-image domain blocklist (fetch pre-filter) ---------


def test_structural_no_image_domain_skipped_from_fetch(monkeypatch, loguru_caplog):
    """A cited URL on a structural no-image domain (instagram) must be
    removed from urls_to_fetch before fetch_content_with_images runs,
    and a STRUCTURAL_SKIP event emitted. The fetch stub must NOT see it.
    """
    fetched_urls: list[str] = []

    def _fake_fetch(urls, **kwargs):
        fetched_urls.extend(urls)
        return {u: {"text": "t", "images": []} for u in urls}

    monkeypatch.setattr(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images",
        _fake_fetch,
    )
    cited_instagram = "https://www.instagram.com/some.post"
    cited_ok = "https://www.example.org/article"
    markdown = (
        "## S\n\n"
        f"x [[1]]({cited_instagram}) y [[2]]({cited_ok})。\n\n"
        "## Sources\n\n"
        f"[1] IG\n   URL: {cited_instagram}\n"
        f"[2] Ex\n   URL: {cited_ok}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    _deferred_image_fill(
        "res-skip",
        final_markdown=markdown,
        results=results,
        settings_snapshot={"report.enable_images": True},
    )
    # Primary contract: instagram URL never reaches fetch.
    assert cited_instagram not in fetched_urls, (
        "instagram URL must be filtered out before fetch"
    )
    assert cited_ok in fetched_urls, (
        "non-blocklisted URL must still be fetched"
    )
    # Secondary contract: STRUCTURAL_SKIP event emitted with domain info.
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "STRUCTURAL_SKIP" in text, (
        f"STRUCTURAL_SKIP event must be emitted; got: {text!r}"
    )
    assert "instagram.com" in text, (
        f"instagram.com must appear in the STRUCTURAL_SKIP line; got: {text!r}"
    )


def test_multilabel_blocklist_entry_matched_via_host_suffix(monkeypatch):
    """wenku.baidu.com is a multi-label blocklist entry that tldextract
    collapses to baidu.com (eTLD+1), so the eTLD+1 check alone misses it.
    The host-suffix fallback must still filter it — AND must NOT filter
    baike.baidu.com (sibling subdomain, NOT in the list, real image source).
    """
    from local_deep_research.web.services import research_service
    fetched_urls: list[str] = []
    def _fake_fetch(urls, **kwargs):
        fetched_urls.extend(urls)
        return {u: {"text": "t", "images": []} for u in urls}
    monkeypatch.setattr(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", _fake_fetch
    )
    wenku = "https://wenku.baidu.com/view/abc.html"
    baike = "https://baike.baidu.com/item/东方明珠/123"
    other = "https://www.example.org/article"
    markdown = (
        "## S\n\n"
        f"x [[1]]({wenku}) [[2]]({baike}) [[3]]({other})。\n\n"
        "## Sources\n\n"
        f"[1] W\n   URL: {wenku}\n"
        f"[2] B\n   URL: {baike}\n"
        f"[3] O\n   URL: {other}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    research_service._deferred_image_fill(
        "res-multilabel", final_markdown=markdown, results=results,
        settings_snapshot={"report.enable_images": True},
    )
    assert wenku not in fetched_urls, (
        "wenku.baidu.com (multi-label entry) must be filtered via host-suffix"
    )
    assert baike in fetched_urls, (
        "baike.baidu.com must NOT be filtered — it is not in the blocklist "
        "and is a real image source (anti-over-block red line)"
    )
    assert other in fetched_urls


# ---------- observability: ATTACH_NEAR_MATCH probe (observe-only) -----------


def test_canonical_attach_on_trailing_slash(loguru_caplog):
    """A cited URL whose only record differs by a trailing slash must
    attach via canonical equality and announce ATTACH_CANONICAL with
    via=trailing_slash — no longer an observe-only near-match.

    Regression for the 2026-08-12 run c325e2a0: 17 trailing-slash
    citations were ATTACH_MISS despite successful fetches.
    """
    cited = "https://example.org/page"           # ref_url, no slash
    record_url = "https://example.org/page/"     # record side, slash
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": record_url}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-canon", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1, (
        "trailing-slash record must attach via canonical equality"
    )
    assert results["findings"][0]["search_results"][0].get("html_content"), (
        "html_content must be written onto the trailing-slash record"
    )
    assert "ATTACH_CANONICAL" in text
    assert cited in text
    assert record_url in text
    assert "via=trailing_slash" in text
    assert "ATTACH_MISS" not in text, (
        "a successfully-attached citation must not also emit ATTACH_MISS"
    )
    assert "ATTACH_NEAR_MATCH" not in text, (
        "a successfully-attached citation must not also emit the "
        "observe-only near-match probe"
    )


def test_no_attach_near_match_when_query_differs(loguru_caplog):
    """Different query values (?id=1 vs ?id=2) must NOT produce a
    near-match — anti-mismatch red line."""
    cited = "https://example.org/p?id=1"
    record_url = "https://example.org/p?id=2"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n[7] Ex\n   URL: {cited}\n"
    ).format(cited=cited)
    results = {"findings": [{"search_results": [{"link": record_url}]}],
               "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            _deferred_image_fill(
                "res-near2", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "ATTACH_NEAR_MATCH" not in text


def test_canonical_attach_steam_question_mark(loguru_caplog):
    """Steam's filedetails?id=<n> vs filedetails/?id=<n> (slash before
    the query separator) is canonical-equal and must attach.

    Regression for 2026-08-12 run c325e2a0: 5 Steam Workshop citations
    (cite_num 30/40/111/112/166-equivalent) were ATTACH_MISS via=trailing_slash.
    """
    cited = "https://steamcommunity.com/sharedfiles/filedetails?id=3506925216"
    record_url = "https://steamcommunity.com/sharedfiles/filedetails/?id=3506925216"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": record_url}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-steam", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1
    assert results["findings"][0]["search_results"][0].get("html_content")
    assert "ATTACH_CANONICAL" in text
    assert record_url in text
    assert "via=trailing_slash" in text
    assert "ATTACH_MISS" not in text


def test_exact_match_takes_precedence_over_canonical(loguru_caplog):
    """When records contain BOTH an exact match and a canonical
    near-neighbor, the exact record is written and ATTACH_CANONICAL
    does NOT fire. Exact wins; filled counts once.
    """
    cited = "https://example.org/page"
    exact_record = {"link": cited}
    slash_record = {"link": "https://example.org/page/"}
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    # Put the slash record first to prove precedence is not just
    # "first record wins" — exact must win regardless of order.
    results = {
        "findings": [{"search_results": [slash_record, exact_record]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-prec", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1
    assert exact_record.get("html_content"), (
        "the exact-match record must receive html_content"
    )
    assert "ATTACH_CANONICAL" not in text, (
        "exact match must not trigger the canonical-attach probe"
    )


def test_attach_canonical_carries_five_key_fields(loguru_caplog):
    """ATTACH_CANONICAL must carry cite_num and ref_url (the five-key
    IMG-TRACE vocabulary), so a single grep reconstructs provenance —
    same audit pattern as tests/images/test_img_trace_audit_events.py.
    """
    cited = "https://example.org/page"
    markdown = (
        "## S\n\n"
        f"x [[42]]({cited})。\n\n"
        "## Sources\n\n"
        "[42] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": "https://example.org/page/"}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            _deferred_image_fill(
                "res-fields", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    canon_lines = [l for l in text.splitlines() if "ATTACH_CANONICAL" in l]
    assert canon_lines, "expected an ATTACH_CANONICAL line"
    line = canon_lines[0]
    assert "cite_num=42" in line
    assert f"ref_url={cited}" in line


def test_attach_miss_still_fires_with_no_near_neighbor(loguru_caplog):
    """A cited URL with no record at all — neither exact nor canonical —
    must still emit ATTACH_MISS. Guards that the canonical pass did not
    accidentally swallow the genuine-miss path.
    """
    cited = "https://orphan.example/never-in-results"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/o.jpg", alt="orphan", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-miss2", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 0
    assert "ATTACH_MISS" in text
    assert "ATTACH_CANONICAL" not in text
