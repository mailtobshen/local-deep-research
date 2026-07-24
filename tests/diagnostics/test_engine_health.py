"""Tests for diagnostics/engine_health.py — preflight allowlist + per-engine categories."""
from unittest.mock import MagicMock, patch

from local_deep_research.diagnostics.engine_health import (
    _ENGINE_CATEGORIES,
    _FALLBACK_ENGINES,
    probe_searxng_engine,
)


# Five engines the user asked to enable and that SearXNG actually
# registers under `use_default_settings: true` (`goo` is not in the upstream
# default engine set, so it was dropped — see verification log).
NEWLY_ENABLED = ["bing", "yandex", "google news"]
# Engines that survived after dropping the known-broken ones (CAPTCHA /
# access denied / 429 — verified via preflight on 2026-07-24).
EXISTING_KEPT = ["google", "google cse", "mwmbl", "wikipedia", "wikidata", "yahoo"]
# Engines removed because they're permanently broken on this proxy IP.
REMOVED = ["duckduckgo", "brave", "mojeek", "qwant", "startpage"]


def test_fallback_engines_contains_all_new_names():
    for n in NEWLY_ENABLED:
        assert n in _FALLBACK_ENGINES, f"{n!r} missing from _FALLBACK_ENGINES"


def test_fallback_engines_keeps_existing_entries():
    for n in EXISTING_KEPT:
        assert n in _FALLBACK_ENGINES, f"regression: {n!r} dropped"


def test_fallback_engines_drops_broken_engines():
    """mojeek/qwant/duckduckgo/brave are permanently broken on this proxy IP.

    Verified via live preflight on 2026-07-24 — all four returned
    CAPTCHA/access-denied/429 every time. Probing them adds ~10s of
    wasted wall-clock per research start.
    """
    for n in REMOVED:
        assert n not in _FALLBACK_ENGINES, (
            f"regression: {n!r} re-added to allowlist (live probe failed)"
        )


def test_engine_categories_has_google_news_only():
    assert _ENGINE_CATEGORIES == {"google news": "news"}


def _probe_with_mock_response(engine_name):
    """Drive probe_searxng_engine with a mocked requests.get; return kwargs."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"Content-Type": "application/json"}
    fake_resp.json.return_value = {"results": [{"url": "https://x"}]}
    with patch(
        "local_deep_research.diagnostics.engine_health.requests.get",
        return_value=fake_resp,
    ) as sg:
        status = probe_searxng_engine("http://localhost:8080", engine_name)
    assert status.status == "ok"
    return sg.call_args.kwargs["params"]


def test_google_news_uses_news_category():
    params = _probe_with_mock_response("google news")
    assert params["categories"] == "news"


def test_other_engines_use_general_category():
    for name in ["bing", "mojeek", "yandex", "qwant", "wikipedia", "wikidata"]:
        params = _probe_with_mock_response(name)
        assert params["categories"] == "general", f"{name} wrong category"


def test_google_news_passes_engine_param_correctly():
    """Sanity: ensure engine name is passed correctly when categories differs."""
    params = _probe_with_mock_response("google news")
    assert params["engines"] == "google news"
    assert params["format"] == "json"
    assert params["q"] == "test"