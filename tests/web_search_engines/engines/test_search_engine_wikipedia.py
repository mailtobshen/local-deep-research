"""
Tests for the WikipediaSearchEngine class.

Tests cover:
- Initialization and configuration
- Preview generation with disambiguation handling
- Full content retrieval
- Summary and page methods
- Error handling
- Bounded-timeout monkeypatch preventing indefinite proxy stalls
"""

from unittest.mock import Mock, patch

import pytest
import requests


class TestWikipediaSearchEngineInit:
    """Tests for WikipediaSearchEngine initialization."""

    def test_init_with_defaults(self):
        """Initialize with default values."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang") as mock_set_lang:
            engine = WikipediaSearchEngine()

            assert engine.max_results == 10
            assert engine.include_content is True
            assert engine.sentences == 5
            mock_set_lang.assert_called_once_with("en")

    def test_init_with_custom_max_results(self):
        """Initialize with custom max_results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine(max_results=25)

            assert engine.max_results == 25

    def test_init_with_custom_language(self):
        """Initialize with custom language."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang") as mock_set_lang:
            WikipediaSearchEngine(language="fr")

            mock_set_lang.assert_called_once_with("fr")

    def test_init_with_custom_sentences(self):
        """Initialize with custom sentences."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine(sentences=10)

            assert engine.sentences == 10

    def test_init_with_include_content_false(self):
        """Initialize with include_content=False."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine(include_content=False)

            assert engine.include_content is False

    def test_init_with_llm(self):
        """Initialize with LLM."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        mock_llm = Mock()
        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine(llm=mock_llm)

            assert engine.llm is mock_llm

    def test_init_with_max_filtered_results(self):
        """Initialize with max_filtered_results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine(max_filtered_results=5)

            assert engine.max_filtered_results == 5


class TestGetPreviews:
    """Tests for _get_previews method."""

    def test_get_previews_returns_results(self):
        """Get previews returns formatted results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=["Python", "Java"]):
                with patch(
                    "wikipedia.summary",
                    side_effect=["Python is a language", "Java is a language"],
                ):
                    engine = WikipediaSearchEngine()

                    previews = engine._get_previews("programming")

                    assert len(previews) == 2
                    assert previews[0]["title"] == "Python"
                    assert previews[0]["snippet"] == "Python is a language"
                    assert "wikipedia.org" in previews[0]["link"]

    def test_get_previews_empty_results(self):
        """Get previews handles empty results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=[]):
                engine = WikipediaSearchEngine()

                previews = engine._get_previews("nonexistent query")

                assert previews == []

    def test_get_previews_handles_disambiguation(self):
        """Get previews handles disambiguation errors."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )
        import wikipedia

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=["Python"]):
                # First call raises disambiguation, second succeeds
                disambig_error = wikipedia.exceptions.DisambiguationError(
                    "Python", ["Python (programming)", "Python (snake)"]
                )
                with patch(
                    "wikipedia.summary",
                    side_effect=[
                        disambig_error,
                        "Python is a programming language",
                    ],
                ):
                    engine = WikipediaSearchEngine()

                    previews = engine._get_previews("python")

                    assert len(previews) == 1
                    assert previews[0]["title"] == "Python (programming)"

    def test_get_previews_handles_page_error(self):
        """Get previews handles page errors gracefully."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )
        import wikipedia

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=["Valid", "Invalid"]):
                page_error = wikipedia.exceptions.PageError("Invalid")
                with patch(
                    "wikipedia.summary",
                    side_effect=["Valid summary", page_error],
                ):
                    engine = WikipediaSearchEngine()

                    previews = engine._get_previews("test")

                    # Only valid result should be returned
                    assert len(previews) == 1
                    assert previews[0]["title"] == "Valid"


class TestSummaryRetry:
    """Proxy-jitter retry behaviour for the summary fetch.

    The ``wikipedia`` library calls a bare ``requests.get`` honoring the proxy
    env vars, so under a flaky proxy we see ``ConnectionError`` /
    ``Timeout`` / ``JSONDecodeError`` (empty body on a 429/5xx). These must be
    retried with backoff; deterministic ``DisambiguationError`` /
    ``PageError`` must NOT be retried.
    """

    def test_transient_connection_error_is_retried_then_succeeds(self):
        """A transient ConnectionError is retried; success on a later attempt."""
        from local_deep_research.web_search_engines.engines import (
            search_engine_wikipedia as mod,
        )
        import requests

        with patch("wikipedia.set_lang"):
            attempts = {"n": 0}

            def flaky(title, sentences=0, auto_suggest=True):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise requests.ConnectionError("proxy reset")
                return "recovered summary"

            with patch("wikipedia.summary", side_effect=flaky):
                engine = mod.WikipediaSearchEngine()
                result = engine.get_summary("Foo")

            assert result == "recovered summary"
            assert attempts["n"] == 3, (
                f"expected 3 attempts (initial + 2 retries), got {attempts['n']}"
            )

    def test_disambiguation_error_is_not_retried(self):
        """DisambiguationError is deterministic and must surface immediately."""
        from local_deep_research.web_search_engines.engines import (
            search_engine_wikipedia as mod,
        )
        import wikipedia

        with patch("wikipedia.set_lang"):
            attempts = {"n": 0}

            def disambig(title, sentences=0, auto_suggest=True):
                attempts["n"] += 1
                raise wikipedia.exceptions.DisambiguationError(
                    "Foo", ["Foo (bar)", "Foo (baz)"]
                )

            with patch("wikipedia.summary", side_effect=disambig):
                engine = mod.WikipediaSearchEngine()
                # get_summary catches DisambiguationError and retries the first
                # option — which here also raises DisambiguationError, so the
                # whole thing should raise after exactly 2 calls (original +
                # first option), NOT be retried for transient reasons.
                with pytest.raises(wikipedia.exceptions.DisambiguationError):
                    engine.get_summary("Foo")

            assert attempts["n"] == 2, (
                f"DisambiguationError must not trigger transient-retry; "
                f"expected 2 calls (title + first option), got {attempts['n']}"
            )

    def test_persistent_timeout_exhausts_retries_and_reraises(self):
        """A persistent Timeout exhausts retries and reraises the last error."""
        from local_deep_research.web_search_engines.engines import (
            search_engine_wikipedia as mod,
        )
        import requests

        with patch("wikipedia.set_lang"):
            attempts = {"n": 0}

            def always_timeout(title, sentences=0, auto_suggest=True):
                attempts["n"] += 1
                raise requests.Timeout("connect timeout")

            with patch("wikipedia.summary", side_effect=always_timeout):
                engine = mod.WikipediaSearchEngine()
                with pytest.raises(requests.Timeout):
                    engine.get_summary("Foo")

            assert attempts["n"] == 3, (
                f"expected 3 attempts before giving up, got {attempts['n']}"
            )

    def test_get_previews_recovers_from_transient_summary_failure(self):
        """A transient summary failure mid-_get_previews is retried, not skipped."""
        from local_deep_research.web_search_engines.engines import (
            search_engine_wikipedia as mod,
        )
        import requests

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=["Python"]):
                attempts = {"n": 0}

                def flaky(title, sentences=0, auto_suggest=True):
                    attempts["n"] += 1
                    if attempts["n"] == 1:
                        raise requests.ConnectionError("proxy reset")
                    return "Python is a language"

                with patch("wikipedia.summary", side_effect=flaky):
                    engine = mod.WikipediaSearchEngine()
                    previews = engine._get_previews("programming")

            assert len(previews) == 1
            assert previews[0]["snippet"] == "Python is a language"
            assert attempts["n"] == 2  # first failed (retried), second succeeded

    def test_get_previews_handles_exception(self):
        """Get previews handles unexpected exceptions."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch(
                "wikipedia.search", side_effect=Exception("Connection error")
            ):
                engine = WikipediaSearchEngine()

                previews = engine._get_previews("test")

                assert previews == []

    def test_get_previews_creates_correct_link(self):
        """Get previews creates correct Wikipedia link."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.search", return_value=["Hello World"]):
                with patch("wikipedia.summary", return_value="Hello summary"):
                    engine = WikipediaSearchEngine()

                    previews = engine._get_previews("test")

                    assert (
                        previews[0]["link"]
                        == "https://en.wikipedia.org/wiki/Hello_World"
                    )


class TestGetFullContent:
    """Tests for _get_full_content method."""

    def test_get_full_content_retrieves_pages(self):
        """Get full content retrieves page data."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        mock_page = Mock()
        mock_page.title = "Python"
        mock_page.url = "https://en.wikipedia.org/wiki/Python"
        mock_page.content = "Full content here"
        mock_page.categories = ["Programming"]
        mock_page.references = ["ref1"]
        mock_page.links = ["link1"]
        mock_page.images = ["image1"]
        mock_page.sections = ["section1"]

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.page", return_value=mock_page):
                engine = WikipediaSearchEngine()

                items = [{"id": "Python", "snippet": "Python summary"}]
                results = engine._get_full_content(items)

                assert len(results) == 1
                assert results[0]["title"] == "Python"
                assert results[0]["content"] == "Full content here"
                assert results[0]["full_content"] == "Full content here"
                assert results[0]["categories"] == ["Programming"]

    def test_get_full_content_handles_error(self):
        """Get full content handles errors gracefully."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )
        import wikipedia

        with patch("wikipedia.set_lang"):
            with patch(
                "wikipedia.page",
                side_effect=wikipedia.exceptions.PageError("Not found"),
            ):
                engine = WikipediaSearchEngine()

                items = [{"id": "Invalid", "snippet": "Preview"}]
                results = engine._get_full_content(items)

                # Should return original item on error
                assert len(results) == 1
                assert results[0]["snippet"] == "Preview"

    def test_get_full_content_no_id(self):
        """Get full content handles items without ID."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine()

            items = [{"title": "Test", "snippet": "No ID here"}]
            results = engine._get_full_content(items)

            assert len(results) == 1
            assert results[0]["snippet"] == "No ID here"


class TestGetSummary:
    """Tests for get_summary method."""

    def test_get_summary_returns_text(self):
        """Get summary returns text."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch(
                "wikipedia.summary", return_value="This is a summary"
            ) as mock_summary:
                engine = WikipediaSearchEngine()

                result = engine.get_summary("Python")

                assert result == "This is a summary"
                mock_summary.assert_called_once_with(
                    "Python", sentences=5, auto_suggest=False
                )

    def test_get_summary_with_custom_sentences(self):
        """Get summary with custom sentences."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            with patch(
                "wikipedia.summary", return_value="Summary"
            ) as mock_summary:
                engine = WikipediaSearchEngine()

                engine.get_summary("Python", sentences=10)

                mock_summary.assert_called_once_with(
                    "Python", sentences=10, auto_suggest=False
                )

    def test_get_summary_handles_disambiguation(self):
        """Get summary handles disambiguation."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )
        import wikipedia

        with patch("wikipedia.set_lang"):
            disambig_error = wikipedia.exceptions.DisambiguationError(
                "Python", ["Python (language)"]
            )
            with patch(
                "wikipedia.summary",
                side_effect=[disambig_error, "Python language summary"],
            ):
                engine = WikipediaSearchEngine()

                result = engine.get_summary("Python")

                assert result == "Python language summary"


class TestGetPage:
    """Tests for get_page method."""

    def test_get_page_returns_full_info(self):
        """Get page returns full information."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        mock_page = Mock()
        mock_page.title = "Python"
        mock_page.url = "https://en.wikipedia.org/wiki/Python"
        mock_page.content = "Full content"
        mock_page.categories = ["Programming"]
        mock_page.references = ["ref1"]
        mock_page.links = ["link1"]
        mock_page.images = ["image1"]
        mock_page.sections = ["section1"]

        with patch("wikipedia.set_lang"):
            with patch("wikipedia.page", return_value=mock_page):
                with patch("wikipedia.summary", return_value="Summary"):
                    engine = WikipediaSearchEngine()

                    result = engine.get_page("Python")

                    assert result["title"] == "Python"
                    assert (
                        result["link"] == "https://en.wikipedia.org/wiki/Python"
                    )
                    assert result["content"] == "Full content"
                    assert result["categories"] == ["Programming"]

    def test_get_page_handles_disambiguation(self):
        """Get page handles disambiguation."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )
        import wikipedia

        mock_page = Mock()
        mock_page.title = "Python (language)"
        mock_page.url = "https://en.wikipedia.org/wiki/Python_(language)"
        mock_page.content = "Content"
        mock_page.categories = []
        mock_page.references = []
        mock_page.links = []
        mock_page.images = []
        mock_page.sections = []

        disambig_error = wikipedia.exceptions.DisambiguationError(
            "Python", ["Python (language)"]
        )

        with patch("wikipedia.set_lang"):
            with patch(
                "wikipedia.page", side_effect=[disambig_error, mock_page]
            ):
                with patch("wikipedia.summary", return_value="Summary"):
                    engine = WikipediaSearchEngine()

                    result = engine.get_page("Python")

                    assert result["title"] == "Python (language)"


class TestSetLanguage:
    """Tests for set_language method."""

    def test_set_language_changes_language(self):
        """Set language changes Wikipedia language."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang") as mock_set_lang:
            engine = WikipediaSearchEngine()

            engine.set_language("de")

            # Called once in init with 'en', once with 'de'
            assert mock_set_lang.call_count == 2
            mock_set_lang.assert_called_with("de")


class TestClassAttributes:
    """Tests for class attributes."""

    def test_is_public(self):
        """WikipediaSearchEngine is marked as public."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        assert WikipediaSearchEngine.is_public is True


class TestRun:
    """Tests for run method (inherited from BaseSearchEngine)."""

    def test_run_returns_results(self):
        """Run returns search results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine()

            with patch.object(
                engine,
                "_get_previews",
                return_value=[
                    {
                        "id": "Python",
                        "title": "Python",
                        "snippet": "Summary",
                        "link": "https://en.wikipedia.org/wiki/Python",
                    }
                ],
            ):
                with patch.object(
                    engine,
                    "_get_full_content",
                    return_value=[
                        {
                            "title": "Python",
                            "snippet": "Summary",
                            "content": "Full",
                        }
                    ],
                ):
                    results = engine.run("python programming")

                    assert len(results) == 1
                    assert results[0]["title"] == "Python"

    def test_run_handles_empty_results(self):
        """Run handles empty results."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            engine = WikipediaSearchEngine()

            with patch.object(engine, "_get_previews", return_value=[]):
                results = engine.run("nonexistent query")

                assert results == []


class TestWikipediaTimeoutPatch:
    """Tests for the bounded-timeout monkeypatch on the wikipedia library.

    The wikipedia PyPI library calls ``requests.get`` with no ``timeout=``,
    so a flaky proxy that never responds blocks forever. The engine init
    patches ``wikipedia.wikipedia._wiki_request`` to inject a timeout so a
    dead connection raises ``requests.Timeout`` (retried by tenacity, then
    skipped) instead of stalling the research thread.
    """

    @pytest.fixture(autouse=True)
    def _restore_wiki_request(self):
        """Restore the original ``_wiki_request`` after each test."""
        from wikipedia import wikipedia as _wp_mod

        original = _wp_mod._wiki_request
        try:
            yield
        finally:
            _wp_mod._wiki_request = original

    def test_init_applies_wikipedia_timeout_patch(self):
        """Constructing the engine patches ``_wiki_request`` with a timeout."""
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            WikipediaSearchEngine,
        )

        with patch("wikipedia.set_lang"):
            WikipediaSearchEngine()

        from wikipedia import wikipedia as _wp_mod

        assert getattr(
            _wp_mod._wiki_request, "_ldr_timeout_patched", False
        ), "init must patch wikipedia._wiki_request with a bounded timeout"

    def test_summary_timeout_raises_instead_of_hanging(self):
        """A timed-out wikipedia call raises within bounded wall-clock.

        Regression for the indefinite-stall bug: before the patch,
        ``requests.get`` had no timeout and blocked forever on a dead proxy.
        After the patch, the inner ``requests.get`` raises
        ``requests.Timeout`` which propagates through
        ``_summary_with_retry`` (tenacity exhausts its 3 attempts quickly
        because a timeout is immediate, not a long wait) and surfaces as a
        ``requests.Timeout``/``ConnectionError`` rather than hanging.
        """
        import time

        from local_deep_research.security.proxy_config import (
            apply_timeout_to_wikipedia_requests,
        )
        from local_deep_research.web_search_engines.engines.search_engine_wikipedia import (
            _summary_with_retry,
        )

        apply_timeout_to_wikipedia_requests(timeout=(1, 1))

        # The patched _wiki_request calls requests.get; force it to raise a
        # timeout so we verify the exception propagates instead of blocking.
        with patch(
            "wikipedia.wikipedia.requests.get",
            side_effect=requests.Timeout("simulated read timeout"),
        ):
            start = time.monotonic()
            with pytest.raises((requests.Timeout, requests.ConnectionError)):
                _summary_with_retry("Pain", 5)
            elapsed = time.monotonic() - start

        # 3 tenacity attempts with short backoff (initial=0.5, max=5.0) must
        # finish well under the old "hang forever" behavior. Generous upper
        # bound to avoid CI flakiness; the point is it terminates.
        assert elapsed < 30, (
            f"summary call took {elapsed:.1f}s — timeout did not propagate "
            "and may be hanging again"
        )
