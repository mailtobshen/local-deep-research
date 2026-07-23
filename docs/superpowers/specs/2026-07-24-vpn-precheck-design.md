# VPN Proxy Pre-research Reachability Check — Design

**Date**: 2026-07-24
**Status**: approved (brainstorming complete)
**Author**: Claude

## Context

Research tasks that require external web access fail in confusing ways when the SS VPN proxy is not active: search engines return garbage, Firecrawl scrape hangs, and the LLM agent errors mid-research. The proxy is configured per-deploy (`app.network.proxy_url` + `app.network.proxy_enabled`, env override `LDR_APP_NETWORK_PROXY_URL`) but a user can launch a research task without realizing the VPN tunnel is down, wasting minutes of compute and producing low-quality results.

This change adds a **synchronous VPN reachability check at the `/api/start_research` entry point**. If the proxy is configured but unreachable, the endpoint returns `422 Unprocessable Entity` with a user-readable error and the research never enters the queue. If the proxy is configured and reachable, the request continues normally and the existing SearXNG/Firecrawl preflight (in `diagnostics/engine_health.py::run_preflight_check`) runs as before inside `run_research_process`.

## Goals & Non-Goals

**Goals**
- Detect "VPN proxy down" before any research work begins, in ≤ 6 seconds
- Block the research task (hard refusal — no "continue anyway" override) when the proxy is down
- Use the same proxy URL the rest of LDR uses (single source of truth: `app.network.proxy_url`)
- Respect user's explicit choice: `proxy_enabled=false` skips the check entirely
- Fail loudly with a user-readable error; never 500 on proxy problems

**Non-Goals**
- Not a replacement for the SearXNG/Firecrawl preflight (which probes engine health, not network reachability)
- Not a "fix VPN for the user" — just refuse the request
- No UI for editing proxy settings (already exists)
- No new dependencies (stdlib only)

## Design

### Architecture

```
Browser → POST /api/start_research
              │
              ▼
    start_research()  (NEW: 1 sync check at top)
              │
              ├─ read settings: app.network.proxy_enabled + app.network.proxy_url
              ├─ if not enabled → skip check, continue
              ├─ if enabled  → check_vpn_proxy(proxy_url)
              │       │
              │       ├─ pass → continue (existing flow unchanged)
              │       └─ raise VPNCheckError → return 422
              │
              ▼ (on success)
    existing: create research row + enqueue research_thread
              │
              ▼
    run_research_process  (existing preflight + research unchanged)
              │
              ▼ 422 (on check failure)
    Browser catch → showAlert(errorData.message, 'error')
```

### Components

#### 1. New module: `src/local_deep_research/security/vpn_precheck.py`

```python
"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _parse_proxy_url(proxy_url: str) -> tuple[str, int]:
    p = urlparse(proxy_url)
    if not p.hostname or not p.port:
        raise VPNCheckError(f"Invalid proxy URL: {proxy_url!r}")
    return p.hostname, p.port


def check_vpn_proxy(
    proxy_url: str,
    *,
    external_probe_url: str = "https://www.google.com/generate_204",
    timeout: float = 3.0,
) -> None:
    """Two-step reachability check. Raises VPNCheckError on failure.

    Step 1: TCP connect to (host, port) — proves proxy process is up.
    Step 2: HTTP GET via proxy to external_probe_url — proves proxy can
            transit to the open internet.

    Both steps must succeed. timeout applies per step (total ≤ 6s).
    """
    host, port = _parse_proxy_url(proxy_url)

    # Step 1
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (socket.timeout, OSError) as e:
        raise VPNCheckError(
            f"VPN proxy port unreachable: {host}:{port} ({e})"
        ) from e

    # Step 2
    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    opener = urllib.request.build_opener(proxy_handler)
    try:
        req = urllib.request.Request(external_probe_url, method="HEAD")
        resp = opener.open(req, timeout=timeout)
        if resp.status not in (200, 204):
            raise VPNCheckError(
                f"VPN proxy returned HTTP {resp.status} from "
                f"{external_probe_url}"
            )
    except VPNCheckError:
        raise
    except Exception as e:
        raise VPNCheckError(
            f"VPN proxy cannot reach external network: {e}"
        ) from e
```

**Why stdlib, not `safe_get`**: `safe_get` performs SSRF validation that includes DNS resolution to a private-IP blocklist. The proxy URL itself is a user-configured trusted address (or comes from env), not untrusted input. Using `safe_get` here would (a) cause a DNS-resolve round-trip on every check and (b) could spuriously fail if the proxy host resolves to a private IP — which is exactly the case in this deployment (172.25.128.1 is a private RFC1918 address). The existing `engine_health.py` already documents this same choice: "Probes use `requests` directly, NOT `safe_get`."

**Why HEAD, not GET**: `https://www.google.com/generate_204` returns 204 with no body, perfect for HEAD. Saves ~1-2 KB of wasted bandwidth and avoids any privacy concern about GET'ing through the proxy.

#### 2. Modify: `src/local_deep_research/web/routes/research_routes.py::start_research()`

Insert a single block at the top of the existing function (after the existing `data = request.json` line):

```python
from ..security.vpn_precheck import check_vpn_proxy, VPNCheckError

proxy_enabled = get_setting_from_snapshot(
    settings_snapshot, "app.network.proxy_enabled", default=False
)
proxy_url = get_setting_from_snapshot(
    settings_snapshot, "app.network.proxy_url", default=""
)

if proxy_enabled and proxy_url:
    try:
        check_vpn_proxy(proxy_url)
        logger.info(
            f"VPN precheck passed: user={current_user.username} url={proxy_url}"
        )
    except VPNCheckError as e:
        logger.warning(
            f"VPN precheck failed: user={current_user.username} "
            f"url={proxy_url} reason={e}"
        )
        return (
            jsonify({
                "status": "error",
                "error": "vpn_proxy_unavailable",
                "message": str(e),
                "hint": "Please enable your VPN proxy and try again.",
            }),
            422,
        )
```

`get_setting_from_snapshot` and `settings_snapshot` are existing locals in `start_research()` (read from the existing captured 546 settings at `start_research:624` per project memory).

#### 3. Frontend: **no change required**

The existing `/api/start_research` caller in `web/static/js/pages/subscriptions.js:316-360` already handles non-200 responses:

```js
} else {
    const errorData = await response.json().catch(() => ({}));
    showAlert(
        i18n.tf('Failed to start research: %s',
                errorData.message || i18n.t('Unknown error')),
        'error'
    );
}
```

The 422 response body `{status: "error", error: "vpn_proxy_unavailable", message: "...", hint: "..."}` flows directly into this `showAlert`. **No frontend change is needed** for this feature.

### Error Handling Matrix

| Scenario | `check_vpn_proxy()` behavior | HTTP | Frontend experience |
|---|---|---|---|
| `proxy_enabled=false` | Not called | 200 | Normal research starts |
| `proxy_enabled=true`, `proxy_url=""` | `_parse_proxy_url` raises VPNCheckError | 422 | toast: "Invalid proxy URL: ''" |
| Proxy process down (port closed) | Step 1 socket fails → VPNCheckError | 422 | toast: "VPN proxy port unreachable: host:port (Connection refused)" |
| Proxy up but can't reach internet | Step 2 urllib fails → VPNCheckError | 422 | toast: "VPN proxy cannot reach external network: ..." |
| Proxy normal | Both steps pass | 200 | Normal research starts |
| Unexpected exception in either step | Wrapped via `raise ... from e` | 422 (not 500) | toast (no leaked stack) |

### Configuration Source

`app.network.proxy_url` and `app.network.proxy_enabled` are the documented "single source of truth" (per `defaults/default_settings.json:230` description). Env overrides `LDR_APP_NETWORK_PROXY_URL` and `LDR_APP_NETWORK_PROXY_ENABLED` flow through the existing settings snapshot mechanism. **No hardcoded URLs anywhere** in this design.

### Settings Read Pattern

`start_research()` already captures a 546-field settings snapshot (`research_routes.py:624`, per memory [[ldr-local-deployment]]). The new check reads two fields from that snapshot via `get_setting_from_snapshot(...)` — consistent with how the rest of `start_research()` reads settings.

## Testing

### New file: `tests/security/test_vpn_precheck.py`

| Test | What it asserts |
|---|---|
| `test_parse_proxy_url_valid` | http://host:8080 → ("host", 8080); socks5h://h:1 → ("h", 1) |
| `test_parse_proxy_url_invalid_raises` | `""`, `"http://"`, `"http://host"` (no port) all raise VPNCheckError |
| `test_check_step1_socket_timeout_raises` | mock `socket.create_connection` raises `socket.timeout` → VPNCheckError with "port unreachable" |
| `test_check_step1_connection_refused_raises` | mock raises `ConnectionRefusedError` → VPNCheckError |
| `test_check_step2_url_error_raises` | step 1 mocked OK, step 2 raises `urllib.error.URLError` → VPNCheckError with "cannot reach external network" |
| `test_check_step2_bad_status_raises` | step 2 returns HTTP 500 → VPNCheckError |
| `test_check_success_returns_none` | Both steps mocked OK → returns None (no exception) |

### New file: `tests/web/routes/test_start_research_vpn.py`

| Test | What it asserts |
|---|---|
| `test_skip_check_when_proxy_disabled` | `proxy_enabled=false` → check_vpn_proxy NEVER called, returns 200 |
| `test_check_skipped_when_url_empty` | `proxy_enabled=true, proxy_url=""` → returns 422 (silent bypass would be worse than visible 422) |
| `test_422_when_check_raises_vpn_check_error` | mock check_vpn_proxy raises VPNCheckError("port down") → 422 + body.error == "vpn_proxy_unavailable" |
| `test_passthrough_when_check_succeeds` | mock check_vpn_proxy returns None → 200 + research created |
| `test_logs_warning_on_check_failure` | caplog asserts warning logged with user + reason |
| `test_no_500_on_unexpected_check_exception` | mock raises RuntimeError → still 422, not 500 |

### Manual Verification

1. `ruff check security/vpn_precheck.py web/routes/research_routes.py tests/security/test_vpn_precheck.py tests/web/routes/test_start_research_vpn.py`
2. Run new pytest files; existing routes tests should still pass (they probably don't currently test VPN — verify in `tests/web/routes/`)
3. `docker restart ldr-local` (hot-mount per [[source-hot-mount-config]])
4. Three e2e scenarios via WebUI:
   - **VPN off + proxy_enabled=false in WebUI**: research starts normally (skip)
   - **VPN on + proxy_enabled=true**: research starts normally (check passes)
   - **VPN enabled in WebUI but proxy down**: temporarily change WebUI `app.network.proxy_url` to `http://127.0.0.1:1` (closed port), click start → see 422 + toast "VPN proxy port unreachable"
5. `docker logs ldr-local --since 1m 2>&1 | grep "VPN precheck"` → see one of `passed` or `failed` lines per attempt

## Files Changed

| File | Type | LOC delta |
|---|---|---|
| `src/local_deep_research/security/vpn_precheck.py` | new | +50 |
| `src/local_deep_research/web/routes/research_routes.py` | modify | +15 |
| `tests/security/test_vpn_precheck.py` | new | +80 |
| `tests/web/routes/test_start_research_vpn.py` | new | +80 |
| Frontend | none | 0 |

**Net**: +225 LOC, no new dependencies, no UI changes, no breaking API changes.

## Out of Scope

- Not changing the SearXNG/Firecrawl preflight (`diagnostics/engine_health.py::run_preflight_check`)
- Not adding "skip VPN check" override button (user confirmed hard refusal)
- Not detecting/fixing VPN issues — just refusing the request
- Not adding caching (a check that runs once per research start is fine; if a user spams the button we just pay 6s each time — that's the correct cost)
- Not adding metrics/telemetry on check failures (logger.warning is sufficient; can be promoted to a metric later)

## Rollback Plan

Revert the 2 modified/added source files via `git revert`. No schema changes, no DB migrations, no env var additions. Frontend already handles 422 via existing `else` branch, so reverting frontend is also a no-op.