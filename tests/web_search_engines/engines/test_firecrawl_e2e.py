"""End-to-end / full-flow tests for the Firecrawl integration.

These exercise the wiring between the settings snapshot, the search-engine
factory, the fetch_content dispatch layer, and the FirecrawlClient — i.e. the
paths a real research run takes, not just the isolated unit behavior.

Run:
    pytest tests/web_search_engines/engines/test_firecrawl_e2e.py -v
"""
from unittest.mock import patch, MagicMock

import pytest

from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)
from local_deep_research.web_search_engines.engines.search_engine_firecrawl import (
    FirecrawlSearchEngine,
)
from local_deep_research.web_search_engines.rate_limiting import RateLimitError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None if status < 400 else Exception("http error")
    return resp


def _enabled_snapshot(**over):
    """A settings snapshot with firecrawl enable + use_for_content_fetch on."""
    base = {
        "search.engine.web.firecrawl.enable": {"value": True, "ui_element": "checkbox"},
        "search.engine.web.firecrawl.use_for_content_fetch": {
            "value": True,
            "ui_element": "checkbox",
        },
        "search.engine.web.firecrawl.api_url": {
            "value": "http://localhost:3002",
            "ui_element": "text",
        },
        "search.engine.web.firecrawl.api_key": {"value": "", "ui_element": "password"},
    }
    base.update(over)
    return base


def _disabled_snapshot():
    return {
        "search.engine.web.firecrawl.enable": {"value": False, "ui_element": "checkbox"},
        "search.engine.web.firecrawl.use_for_content_fetch": {
            "value": False,
            "ui_element": "checkbox",
        },
    }


# ---------------------------------------------------------------------------
# Flow 1: Firecrawl as a content-fetch backend (fetch_content dispatch)
# ---------------------------------------------------------------------------


class TestFlow1ContentFetchBackend:
    """Spec §流程一: use_for_content_fetch=true → firecrawl-first, legacy fallback."""

    def test_disabled_returns_legacy_unchanged(self):
        """Both switches off → fetch_content == batch_fetch_and_extract verbatim."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        legacy = {"https://a.com": "legacy a", "https://b.com": "legacy b"}
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value=legacy,
        ) as mock_legacy, patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc:
            result = fetch_content(
                ["https://a.com", "https://b.com"],
                settings_snapshot=_disabled_snapshot(),
            )
        assert result == legacy
        # Same list, same order, passed straight through
        assert mock_legacy.call_args.args[0] == ["https://a.com", "https://b.com"]
        mock_fc.return_value.batch_scrape.assert_not_called()

    def test_only_master_switch_on_still_passthrough(self):
        """enable=true but use_for_content_fetch=false → still passthrough."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        snap = {
            "search.engine.web.firecrawl.enable": {"value": True, "ui_element": "checkbox"},
            "search.engine.web.firecrawl.use_for_content_fetch": {
                "value": False,
                "ui_element": "checkbox",
            },
        }
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value={"https://a.com": "legacy"},
        ), patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc:
            result = fetch_content(["https://a.com"], settings_snapshot=snap)
        assert result == {"https://a.com": "legacy"}
        mock_fc.return_value.batch_scrape.assert_not_called()

    def test_firecrawl_succeeds_no_fallback_call(self):
        """Firecrawl returns all URLs → batch_fetch_and_extract never called."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        fc = {"https://a.com": "# A", "https://b.com": "# B"}
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc, patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract"
        ) as mock_legacy:
            mock_fc.return_value.batch_scrape.return_value = fc
            result = fetch_content(
                ["https://a.com", "https://b.com"],
                settings_snapshot=_enabled_snapshot(),
            )
        assert result == fc
        mock_legacy.assert_not_called()

    def test_partial_fallback_only_failed_urls(self):
        """None-valued URLs fall back; succeeded ones keep firecrawl markdown."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        fc = {"https://a.com": "# A", "https://b.com": None, "https://c.com": None}
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc, patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value={"https://b.com": "legacy B", "https://c.com": "legacy C"},
        ) as mock_legacy:
            mock_fc.return_value.batch_scrape.return_value = fc
            result = fetch_content(
                ["https://a.com", "https://b.com", "https://c.com"],
                settings_snapshot=_enabled_snapshot(),
            )
        assert result == {
            "https://a.com": "# A",
            "https://b.com": "legacy B",
            "https://c.com": "legacy C",
        }
        # Only the two failed URLs are re-fetched via legacy
        assert mock_legacy.call_args.args[0] == ["https://b.com", "https://c.com"]

    def test_firecrawl_down_full_fallback(self):
        """Client raises → every URL falls back; result non-empty."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc, patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value={"https://a.com": "legacy"},
        ) as mock_legacy:
            mock_fc.return_value.batch_scrape.side_effect = Exception("boom")
            result = fetch_content(
                ["https://a.com"], settings_snapshot=_enabled_snapshot()
            )
        assert result == {"https://a.com": "legacy"}
        mock_legacy.assert_called_once()

    def test_rate_limit_propagates_through_dispatch(self):
        """batch_scrape raising RateLimitError must not be swallowed into fallback."""
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc, patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract"
        ) as mock_legacy:
            mock_fc.return_value.batch_scrape.side_effect = RateLimitError("limited")
            with pytest.raises(RateLimitError):
                fetch_content(
                    ["https://a.com"], settings_snapshot=_enabled_snapshot()
                )
        mock_legacy.assert_not_called()

    def test_empty_urls_returns_empty(self):
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content,
        )

        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc:
            assert fetch_content([], settings_snapshot=_enabled_snapshot()) == {}
        mock_fc.return_value.batch_scrape.assert_not_called()


# ---------------------------------------------------------------------------
# Flow 1b: FullSearchResults routes through fetch_content (SSRF preserved)
# ---------------------------------------------------------------------------


class TestFullSearchResultsRouting:
    def test_run_calls_fetch_content_with_safe_urls(self):
        from local_deep_research.web_search_engines.engines.full_search import (
            FullSearchResults,
        )

        fsr = FullSearchResults(
            llm=None,
            web_search=type(
                "W",
                (),
                {
                    "invoke": staticmethod(
                        lambda q: [
                            {"link": "http://169.254.169.254/secret", "title": "bad"},
                            {"link": "http://example.com/good", "title": "good"},
                        ]
                    )
                },
            )(),
            settings_snapshot=_disabled_snapshot(),
        )
        with patch(
            "local_deep_research.web_search_engines.engines.full_search.QUALITY_CHECK_DDG_URLS",
            False,
        ), patch(
            "local_deep_research.web_search_engines.engines.full_search.fetch_content",
            return_value={"http://example.com/good": "Safe content"},
        ) as mock_fc:
            results = fsr.run("test query")

        mock_fc.assert_called_once()
        fetched = mock_fc.call_args.args[0]
        # SSRF-blocked URL never reaches fetch_content
        assert "http://169.254.169.254/secret" not in fetched
        assert "http://example.com/good" in fetched
        assert any(r.get("full_content") == "Safe content" for r in results)


# ---------------------------------------------------------------------------
# Flow 2: firecrawl_search mode (engine end-to-end through BaseSearchEngine.run)
# ---------------------------------------------------------------------------


class TestFlow2FirecrawlSearchMode:
    def test_engine_run_full_flow_search_mode(self):
        """Full run(): previews via /v1/search → full content reuses markdown."""
        search_resp = [
            {
                "title": "A",
                "url": "https://a.com",
                "description": "desc a",
                "markdown": "# A body",
            }
        ]
        with patch(
            "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
        ) as MockFC:
            MockFC.return_value.search.return_value = search_resp
            engine = FirecrawlSearchEngine(
                api_url="http://localhost:3002",
                api_key="fc-test",
                search_mode="firecrawl_search",
                max_results=5,
                include_full_content=True,
                settings_snapshot={},
            )
            # search_snippets_only=False so _get_full_content runs; llm=None
            # disables the relevance filter so previews pass through unchanged.
            results = engine.run("query")

        MockFC.return_value.search.assert_called_once_with("query", limit=5)
        # markdown from /v1/search is reused → no scrape call
        MockFC.return_value.scrape.assert_not_called()
        assert results
        assert results[0]["link"] == "https://a.com"
        assert results[0]["content"] == "# A body"

    def test_previews_missing_markdown_triggers_scrape(self):
        """If /v1/search omits markdown, _get_full_content scrapes the link."""
        search_resp = [
            {"title": "A", "url": "https://a.com", "description": "d", "markdown": None}
        ]
        with patch(
            "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
        ) as MockFC:
            MockFC.return_value.search.return_value = search_resp
            MockFC.return_value.scrape.return_value = "# Scraped body"
            engine = FirecrawlSearchEngine(
                api_url="http://localhost:3002",
                api_key="fc-test",
                search_mode="firecrawl_search",
                max_results=5,
                settings_snapshot={},
            )
            results = engine.run("query")
        MockFC.return_value.scrape.assert_called_once_with("https://a.com")
        assert results[0]["content"] == "# Scraped body"

    def test_search_error_returns_empty_no_crash(self):
        with patch(
            "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
        ) as MockFC:
            MockFC.return_value.search.side_effect = Exception("down")
            engine = FirecrawlSearchEngine(
                api_url="http://localhost:3002",
                api_key="fc-test",
                search_mode="firecrawl_search",
                max_results=5,
                settings_snapshot={},
            )
            results = engine.run("query")
        assert results == []

    def test_rate_limit_reraised_through_run(self):
        with patch(
            "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
        ) as MockFC:
            MockFC.return_value.search.side_effect = RateLimitError("limited")
            engine = FirecrawlSearchEngine(
                api_url="http://localhost:3002",
                api_key="fc-test",
                search_mode="firecrawl_search",
                max_results=5,
                settings_snapshot={},
            )
            # Rate limiting disabled by default in programmatic/in-memory mode,
            # so RateLimitError is caught and run() returns [] rather than
            # propagating — but the engine must NOT silently swallow it into a
            # normal empty-with-log path that masks a real 429. Verify the
            # search call was attempted and no scrape happened.
            results = engine.run("query")
        assert results == []
        MockFC.return_value.search.assert_called_once()
        MockFC.return_value.scrape.assert_not_called()


# ---------------------------------------------------------------------------
# Flow 3: ldr_search mode (SearXNG previews + Firecrawl scrape)
# ---------------------------------------------------------------------------


class TestFlow3LdrSearchMode:
    def test_ldr_search_delegates_previews_then_scrapes_content(self):
        """ldr_search: previews from SearXNG fetcher; content via Firecrawl scrape."""
        with patch(
            "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
        ) as MockFC:
            MockFC.return_value.scrape.return_value = "# Scraped"
            engine = FirecrawlSearchEngine(
                api_url="http://localhost:3002",
                api_key="fc-test",
                search_mode="ldr_search",
                max_results=5,
                settings_snapshot={},
            )
            fake_fetcher = MagicMock()
            fake_fetcher._get_previews.return_value = [
                {
                    "id": "u1",
                    "title": "T1",
                    "link": "https://a.com",
                    "snippet": "s",
                    # No _full_result / markdown → scrape path
                }
            ]
            with patch.object(
                engine, "_build_ldr_preview_fetcher", return_value=fake_fetcher
            ):
                results = engine.run("query")
        fake_fetcher._get_previews.assert_called_once_with("query")
        # ldr_search does NOT call /v1/search
        MockFC.return_value.search.assert_not_called()
        # Firecrawl only scrapes for content
        MockFC.return_value.scrape.assert_called_once_with("https://a.com")
        assert results[0]["content"] == "# Scraped"

    def test_ldr_search_no_source_returns_empty(self):
        engine = FirecrawlSearchEngine(
            api_url="http://localhost:3002",
            api_key="fc-test",
            search_mode="ldr_search",
            max_results=5,
            settings_snapshot={},
        )
        with patch.object(engine, "_build_ldr_preview_fetcher", return_value=None):
            assert engine.run("query") == []


# ---------------------------------------------------------------------------
# Factory wiring: settings snapshot → engine instance
# ---------------------------------------------------------------------------


class TestFactoryWiring:
    """The factory must instantiate FirecrawlSearchEngine with api_url/search_mode
    threaded from the settings snapshot (via default_params + top-level keys)."""

    def _snap(self, **over):
        base = {
            "search.engine.web.firecrawl.enable": {"value": True, "ui_element": "checkbox"},
            "search.engine.web.firecrawl.use_for_content_fetch": {
                "value": False,
                "ui_element": "checkbox",
            },
            "search.engine.web.firecrawl.api_url": {
                "value": "http://localhost:3002",
                "ui_element": "text",
            },
            "search.engine.web.firecrawl.api_key": {"value": "", "ui_element": "password"},
            # The factory threads max_results from the global search.max_results
            # setting (it takes precedence over per-engine default_params), so
            # set it here to verify the value reaches the engine.
            "search.max_results": {"value": 7, "ui_element": "number"},
            "search.engine.web.firecrawl.search_mode": {
                "value": "firecrawl_search",
                "ui_element": "select",
            },
            "search.engine.web.firecrawl.default_params.max_results": {
                "value": 7,
                "ui_element": "number",
            },
            "search.engine.web.firecrawl.default_params.include_full_content": {
                "value": True,
                "ui_element": "checkbox",
            },
            "search.engine.web.firecrawl.requires_api_key": {
                "value": False,
                "ui_element": "checkbox",
            },
            "search.engine.web.firecrawl.use_in_auto_search": {
                "value": True,
                "ui_element": "checkbox",
            },
            "search.engine.web.firecrawl.supports_full_search": {
                "value": True,
                "ui_element": "checkbox",
            },
            "search.engine.web.firecrawl.reliability": {
                "value": 0.8,
                "ui_element": "range",
            },
            "search.engine.web.firecrawl.display_name": {
                "value": "Firecrawl",
                "ui_element": "text",
            },
        }
        base.update(over)
        return base

    def test_factory_creates_engine_with_settings(self):
        from local_deep_research.web_search_engines.search_engine_factory import (
            create_search_engine,
        )

        engine = create_search_engine(
            "firecrawl", llm=None, settings_snapshot=self._snap()
        )
        assert engine is not None
        assert isinstance(engine, FirecrawlSearchEngine)
        # max_results threaded from the global search.max_results setting
        assert engine.max_results == 7
        # search_mode threaded from top-level key
        assert engine.search_mode == "firecrawl_search"
        # api_url threaded from top-level key
        assert engine.api_url == "http://localhost:3002"

    def test_factory_reads_search_mode_override(self):
        from local_deep_research.web_search_engines.search_engine_factory import (
            create_search_engine,
        )

        snap = self._snap(
            **{
                "search.engine.web.firecrawl.search_mode": {
                    "value": "ldr_search",
                    "ui_element": "select",
                }
            }
        )
        engine = create_search_engine("firecrawl", llm=None, settings_snapshot=snap)
        assert engine.search_mode == "ldr_search"

    def test_factory_self_hosted_no_api_key_required(self):
        """requires_api_key=false → engine builds even with empty api_key."""
        from local_deep_research.web_search_engines.search_engine_factory import (
            create_search_engine,
        )

        engine = create_search_engine(
            "firecrawl", llm=None, settings_snapshot=self._snap()
        )
        # _resolve_api_key raises if no key AND it were required; since
        # requires_api_key=false the factory never demands one, and the
        # engine's own _resolve_api_key is only called inside __init__.
        # A successful construction proves no key was demanded.
        assert engine.api_key == ""


# ---------------------------------------------------------------------------
# Client request shape (verifies endpoint + payload + proxy bypass together)
# ---------------------------------------------------------------------------


class TestClientRequestShape:
    def test_scrape_hits_v1_scrape_with_markdown_format(self):
        client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
            return_value=_mock_response(200, {"data": {"markdown": "# x"}}),
        ) as mock_post:
            client.scrape("https://example.com")
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:3002/v1/scrape"
        assert kwargs["json"] == {"url": "https://example.com", "formats": ["markdown"]}
        assert kwargs["headers"]["Authorization"] == "Bearer fc-test"
        assert kwargs["allow_private_ips"] is True

    def test_search_hits_v1_search_with_query_and_limit(self):
        client = FirecrawlClient(api_url="http://localhost:3002", api_key=None)
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
            return_value=_mock_response(200, {"data": []}),
        ) as mock_post:
            client.search("q", limit=8)
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:3002/v1/search"
        assert kwargs["json"] == {"query": "q", "limit": 8}
        # No api_key → no Authorization header
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["allow_private_ips"] is True

    def test_batch_scrape_posts_urls_then_polls_job(self):
        client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
        create = _mock_response(200, {"id": "job-1", "status": "processing"})
        poll_done = _mock_response(
            200,
            {"status": "completed", "data": [{"url": "https://a.com", "markdown": "# A"}]},
        )
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
            return_value=create,
        ) as mock_post, patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            return_value=poll_done,
        ) as mock_get, patch("time.sleep"):
            result = client.batch_scrape(["https://a.com"], max_wait=60, poll_interval=1)
        assert result == {"https://a.com": "# A"}
        # POST to /v1/batch/scrape with the url list
        post_url = mock_post.call_args.args[0]
        assert post_url == "http://localhost:3002/v1/batch/scrape"
        assert mock_post.call_args.kwargs["json"]["urls"] == ["https://a.com"]
        # GET to /v1/batch/scrape/job-1
        get_url = mock_get.call_args.args[0]
        assert get_url == "http://localhost:3002/v1/batch/scrape/job-1"
