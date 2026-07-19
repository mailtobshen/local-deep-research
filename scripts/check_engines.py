#!/usr/bin/env python3
"""Diagnostic CLI: probe SearXNG backends + Firecrawl service health.

Runs outside the web/research thread, so it cannot read the encrypted per-user
settings DB. Instead it builds a settings snapshot from environment variables
(the same ``LDR_*`` overrides the container sets) with sensible defaults, then
delegates to ``local_deep_research.diagnostics.engine_health``.

Usage (inside the ldr-local container):
    /install/.venv/bin/python /app/scripts/check_engines.py

    # or override the SearXNG URL explicitly:
    LDR_SEARCH_ENGINE_WEB_SEARXNG_DEFAULT_PARAMS_INSTANCE_URL=http://searxng-ldr:8080 \\
        /install/.venv/bin/python /app/scripts/check_engines.py

Exit code: 0 if at least one probed engine/service is OK, 1 otherwise.
"""
import os
import sys

from local_deep_research.diagnostics.engine_health import (
    DEFAULT_FIRECRAWL_URL,
    DEFAULT_SEARXNG_URL,
    format_status_table,
    run_preflight_check,
)

# Setting key -> environment variable name (mirrors SettingsManager env mapping).
_ENV_FOR = {
    "search.engine.web.searxng.default_params.instance_url": (
        "LDR_SEARCH_ENGINE_WEB_SEARXNG_DEFAULT_PARAMS_INSTANCE_URL",
        DEFAULT_SEARXNG_URL,
    ),
    "search.engine.web.firecrawl.enable": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_ENABLE",
        "",
    ),
    "search.engine.web.firecrawl.api_url": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_API_URL",
        DEFAULT_FIRECRAWL_URL,
    ),
    "search.engine.web.firecrawl.api_key": (
        "LDR_SEARCH_ENGINE_WEB_FIRECRAWL_API_KEY",
        "",
    ),
}


def _snapshot_from_env() -> dict:
    """Build a minimal settings snapshot from LDR_* environment variables."""
    snap = {}
    for key, (env_var, default) in _ENV_FOR.items():
        val = os.getenv(env_var, default)
        if val in ("", None):
            continue
        # Normalize booleans for the enable flag.
        if key.endswith(".enable"):
            snap[key] = str(val).lower() in ("1", "true", "yes", "on")
        else:
            snap[key] = val
    return snap


def main() -> int:
    snapshot = _snapshot_from_env()
    statuses = run_preflight_check(settings_snapshot=snapshot)
    print(format_status_table(statuses))
    ok = sum(1 for s in statuses if s.status == "ok")
    active = sum(1 for s in statuses if s.status != "skipped")
    if active == 0:
        print("\n没有可探测的引擎/服务。")
        return 1
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
