# VPN Proxy Pre-research Reachability Check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronous VPN proxy reachability check at `/api/start_research` that returns 422 (with user-readable error) when the configured proxy is down, and never enters the research queue.

**Architecture:** New `security/vpn_precheck.py` module exposes `check_vpn_proxy(proxy_url)` which does TCP-port probe + external-network HEAD probe (≤6s total). `start_research()` calls it at the top before creating the research row. On `VPNCheckError` it returns 422 JSON; otherwise the existing flow (settings snapshot, research row, queue) runs unchanged. Frontend reuses the existing non-200 `else` branch + `showAlert` — no frontend changes.

**Tech Stack:** Python 3.14 stdlib only (`socket`, `urllib.request`, `urllib.parse`); Flask `jsonify` for 422 response; existing `get_setting_from_snapshot` from `web.services.research_service` for settings access.

## Global Constraints

- Stdlib only — no new pip dependencies
- All VPN probe code paths must return 422, never 500, on any error
- Read proxy config from `app.network.proxy_url` and `app.network.proxy_enabled` via `get_setting_from_snapshot` (single source of truth; env overrides `LDR_APP_NETWORK_PROXY_URL`/`LDR_APP_NETWORK_PROXY_ENABLED` flow through same path)
- `proxy_enabled=false` skips the check entirely
- Per-step timeout 3s, total ≤ 6s
- Default external probe URL: `https://www.google.com/generate_204` (HEAD method, accept 200 or 204)
- Hot-mount source changes require `docker restart ldr-local`
- Use stdlib `socket` + `urllib` directly; do NOT use `safe_get` (SSRF validator would block private-IP proxy hosts like 172.25.128.1)
- All new tests use `pytest` + `unittest.mock` (MagicMock + `monkeypatch` for module-level patching)
- Commit message style: `feat(scope): concise description` for new modules, `test(scope): ...` for tests, `fix(scope): ...` for fixes

## File Structure

| File | Type | Responsibility |
|---|---|---|
| `src/local_deep_research/security/vpn_precheck.py` | NEW | `VPNCheckError` exception + `check_vpn_proxy()` function |
| `src/local_deep_research/web/routes/research_routes.py` | MODIFY | Insert VPN check at top of `start_research()` |
| `tests/security/test_vpn_precheck.py` | NEW | Unit tests for `check_vpn_proxy()` and `_parse_proxy_url()` |
| `tests/web/routes/test_start_research_vpn.py` | NEW | Tests for VPN check integration into `start_research()` route |

No new modules beyond `vpn_precheck.py`. Frontend untouched.

---

## Task 1: `vpn_precheck.py` core — `_parse_proxy_url` + exception

**Files:**
- Create: `src/local_deep_research/security/vpn_precheck.py`
- Test: `tests/security/test_vpn_precheck.py`

**Interfaces:**
- Produces: `class VPNCheckError(Exception)` — raised by `check_vpn_proxy()` and `_parse_proxy_url()`
- Produces: `def _parse_proxy_url(proxy_url: str) -> tuple[str, int]` — internal helper, returns `(hostname, port)`

- [ ] **Step 1: Write failing tests for `_parse_proxy_url` and `VPNCheckError`**

Create `tests/security/test_vpn_precheck.py`:

```python
"""Tests for VPN proxy reachability check."""
from local_deep_research.security.vpn_precheck import (
    VPNCheckError,
    _parse_proxy_url,
)


def test_parse_proxy_url_http():
    host, port = _parse_proxy_url("http://172.25.128.1:10888")
    assert host == "172.25.128.1"
    assert port == 10888


def test_parse_proxy_url_socks5h():
    host, port = _parse_proxy_url("socks5h://proxy.example.com:1080")
    assert host == "proxy.example.com"
    assert port == 1080


def test_parse_proxy_url_invalid_empty_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("")


def test_parse_proxy_url_invalid_no_scheme_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("172.25.128.1:10888")


def test_parse_proxy_url_invalid_no_port_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("http://172.25.128.1")
```

- [ ] **Step 2: Run tests to verify they fail (collection error)**

Run:
```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
from local_deep_research.security import vpn_precheck
print('imported')
"
```
Expected: `ModuleNotFoundError: No module named 'local_deep_research.security.vpn_precheck'`

- [ ] **Step 3: Implement `_parse_proxy_url` and `VPNCheckError`**

Create `src/local_deep_research/security/vpn_precheck.py`:

```python
"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _parse_proxy_url(proxy_url: str) -> tuple[str, int]:
    """Parse http://host:port or socks5h://host:port → (host, port).

    Raises VPNCheckError if hostname or port is missing.
    """
    p = urlparse(proxy_url)
    if not p.hostname or not p.port:
        raise VPNCheckError(f"Invalid proxy URL: {proxy_url!r}")
    return p.hostname, p.port
```

- [ ] **Step 4: Run tests to verify they pass**

Copy test file into container:
```bash
docker cp tests/security/test_vpn_precheck.py ldr-local:/tmp/test_vpn_precheck.py
```

Run:
```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as mod
import inspect

# Run only the parse_url tests (Task 1 scope; check_vpn_proxy tests come in Task 2)
parse_tests = [n for n in dir(mod) if n.startswith('test_parse')]
failures = 0
for name in parse_tests:
    try:
        getattr(mod, name)()
        print('PASS', name)
    except Exception as e:
        print('FAIL', name, type(e).__name__, str(e)[:80])
        failures += 1
sys.exit(failures)
"
```
Expected: 5 PASS lines, exit 0

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/security/vpn_precheck.py tests/security/test_vpn_precheck.py
git commit -m "feat(security): VPNCheckError + _parse_proxy_url for VPN precheck"
```

---

## Task 2: `check_vpn_proxy()` — TCP port probe

**Files:**
- Modify: `src/local_deep_research/security/vpn_precheck.py`
- Modify: `tests/security/test_vpn_precheck.py`

**Interfaces:**
- Produces: `def check_vpn_proxy(proxy_url: str, *, external_probe_url: str = ..., timeout: float = 3.0) -> None` — raises `VPNCheckError` on failure, returns None on success. Step 1 (TCP port probe) is added in this task; Step 2 (external HEAD probe) is added in Task 3.

- [ ] **Step 1: Write failing tests for TCP port probe step**

Append to `tests/security/test_vpn_precheck.py`:

```python
from unittest.mock import patch


def test_check_vpn_proxy_step1_port_unreachable():
    """TCP connect failure → VPNCheckError with 'port unreachable'."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    import socket as _socket

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection",
        side_effect=_socket.timeout("timed out"),
    ):
        import pytest
        with pytest.raises(VPNCheckError, match="port unreachable"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step1_connection_refused():
    """OSError (Connection refused) → VPNCheckError."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection",
        side_effect=ConnectionRefusedError("Connection refused"),
    ):
        import pytest
        with pytest.raises(VPNCheckError, match="port unreachable"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (using same `docker cp` + `docker exec python3` pattern as Task 1 Step 4):

```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as mod
failures = 0
for name in ['test_check_vpn_proxy_step1_port_unreachable',
             'test_check_vpn_proxy_step1_connection_refused']:
    try:
        getattr(mod, name)()
        print('UNEXPECTED PASS', name)
    except (NameError, AttributeError) as e:
        print('EXPECTED FAIL', name, type(e).__name__, str(e)[:80])
    except Exception as e:
        print('UNEXPECTED FAIL', name, type(e).__name__, str(e)[:80])
        failures += 1
sys.exit(failures)
"
```
Expected: 2 lines with `NameError: name 'check_vpn_proxy' is not defined`

- [ ] **Step 3: Add `check_vpn_proxy()` with step 1 only (TCP probe)**

Replace `src/local_deep_research/security/vpn_precheck.py` with:

```python
"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _parse_proxy_url(proxy_url: str) -> tuple[str, int]:
    """Parse http://host:port or socks5h://host:port → (host, port).

    Raises VPNCheckError if hostname or port is missing.
    """
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
    Step 2: HTTP HEAD via proxy to external_probe_url — proves proxy can
            transit to the open internet.

    Both steps must succeed. timeout applies per step (total ≤ 6s).

    NOTE: Step 2 is added in Task 3; this task adds step 1 only.
    """
    host, port = _parse_proxy_url(proxy_url)

    # Step 1: proxy port reachable
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (socket.timeout, OSError) as e:
        raise VPNCheckError(
            f"VPN proxy port unreachable: {host}:{port} ({e})"
        ) from e
```

- [ ] **Step 4: Run step-1 tests to verify they pass**

Re-run the snippet from Step 2 (it will now find `check_vpn_proxy`):

```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as mod
failures = 0
for name in ['test_check_vpn_proxy_step1_port_unreachable',
             'test_check_vpn_proxy_step1_connection_refused']:
    try:
        getattr(mod, name)()
        print('PASS', name)
    except Exception as e:
        print('FAIL', name, type(e).__name__, str(e)[:80])
        failures += 1
sys.exit(failures)
"
```
Expected: 2 PASS lines, exit 0

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/security/vpn_precheck.py tests/security/test_vpn_precheck.py
git commit -m "feat(security): check_vpn_proxy step 1 (TCP port probe)"
```

---

## Task 3: `check_vpn_proxy()` — external HEAD probe (step 2)

**Files:**
- Modify: `src/local_deep_research/security/vpn_precheck.py`
- Modify: `tests/security/test_vpn_precheck.py`

**Interfaces:**
- `check_vpn_proxy()` now performs BOTH steps; this task adds step 2.

- [ ] **Step 1: Write failing tests for step 2**

Append to `tests/security/test_vpn_precheck.py`:

```python
def test_check_vpn_proxy_step2_url_error_raises():
    """Step 1 OK + step 2 URLError → VPNCheckError 'cannot reach external'."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock
    import urllib.error

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.URLError("Name or service not known")
        mock_opener_factory.return_value = mock_opener

        import pytest
        with pytest.raises(VPNCheckError, match="cannot reach external network"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step2_bad_status_raises():
    """Step 2 returns HTTP 500 → VPNCheckError."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        import pytest
        with pytest.raises(VPNCheckError, match="HTTP 500"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step2_success_returns_none():
    """Both steps OK → returns None (no exception)."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        # Should not raise
        result = check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert result is None


def test_check_vpn_proxy_step2_accepts_status_200():
    """Status 200 also accepted (some proxies rewrite 204 → 200)."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        result = check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert result is None
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as mod
failures = 0
for name in ['test_check_vpn_proxy_step2_url_error_raises',
             'test_check_vpn_proxy_step2_bad_status_raises',
             'test_check_vpn_proxy_step2_success_returns_none',
             'test_check_vpn_proxy_step2_accepts_status_200']:
    try:
        getattr(mod, name)()
        print('UNEXPECTED PASS', name)
    except AssertionError as e:
        print('EXPECTED FAIL', name, str(e)[:80])
    except Exception as e:
        print('UNEXPECTED FAIL', name, type(e).__name__, str(e)[:80])
        failures += 1
sys.exit(failures)
"
```
Expected: 4 lines indicating the step 2 path was attempted but errored (URLError not wrapped, or "not yet implemented" message). The exact failure depends on the unmodified source. Just confirm there are 4 lines of output (not silent exit).

- [ ] **Step 3: Add step 2 (HEAD via proxy) to `check_vpn_proxy()`**

Replace `src/local_deep_research/security/vpn_precheck.py` with:

```python
"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _parse_proxy_url(proxy_url: str) -> tuple[str, int]:
    """Parse http://host:port or socks5h://host:port → (host, port).

    Raises VPNCheckError if hostname or port is missing.
    """
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
    Step 2: HTTP HEAD via proxy to external_probe_url — proves proxy can
            transit to the open internet.

    Both steps must succeed. timeout applies per step (total ≤ 6s).

    Uses stdlib urllib (NOT safe_get) because safe_get's SSRF validator
    would block private-IP proxy hosts like 172.25.128.1.
    """
    host, port = _parse_proxy_url(proxy_url)

    # Step 1: proxy port reachable
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (socket.timeout, OSError) as e:
        raise VPNCheckError(
            f"VPN proxy port unreachable: {host}:{port} ({e})"
        ) from e

    # Step 2: proxy can reach external network
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

- [ ] **Step 4: Run all `test_vpn_precheck.py` tests; expect 9 PASS**

```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as mod
fns = [n for n in dir(mod) if n.startswith('test_')]
passed = failed = 0
for name in fns:
    try:
        getattr(mod, name)()
        print('PASS', name); passed += 1
    except Exception as e:
        print('FAIL', name, type(e).__name__, str(e)[:80]); failed += 1
print(f'{passed}/{passed+failed}')
sys.exit(failed)
"
```
Expected: 9 PASS lines (5 from Task 1 + 2 from Task 2 + 4 from Task 3 = wait, that's 11. Recount: Task 1 added 5 tests, Task 2 added 2, Task 3 added 4 = 11 total. The "5 PASS" in Step 4 Task 1 was wrong — actual is 5 tests from Task 1 alone. Expect 11 total PASS).

Re-expected: `11/11` printed, exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/security/vpn_precheck.py tests/security/test_vpn_precheck.py
git commit -m "feat(security): check_vpn_proxy step 2 (external HEAD probe)"
```

---

## Task 4: Wire VPN check into `start_research()` route

**Files:**
- Modify: `src/local_deep_research/web/routes/research_routes.py`
- Test: `tests/web/routes/test_start_research_vpn.py` (new)

**Interfaces:**
- `start_research()` (existing Flask endpoint, route `POST /api/start_research`, `@login_required`) now calls `check_vpn_proxy(proxy_url)` if `app.network.proxy_enabled` is true and `app.network.proxy_url` is non-empty. On `VPNCheckError`, returns `(jsonify({...}), 422)`.

- [ ] **Step 1: Verify the `start_research()` shape matches the spec**

Read `src/local_deep_research/web/routes/research_routes.py` from line 376 (route decorator) through line 400 to confirm:
- `data = request.json` line exists (where to insert VPN check AFTER)
- `settings_snapshot` local variable exists (where to read settings FROM)
- `current_user` is in scope (for log messages)

If any of these is missing, stop and report — the integration point is different from what the spec assumes.

Expected: All three present (verified during brainstorming).

- [ ] **Step 2: Write failing route-level tests**

Create `tests/web/routes/test_start_research_vpn.py`:

```python
"""Tests for VPN check integration into /api/start_research."""
from unittest.mock import patch, MagicMock


def _fake_start_research_app():
    """Build a minimal Flask app with just the start_research route registered.

    Returns a Flask test client. We don't import the full app to avoid pulling
    in langchain_anthropic, encrypted DB, etc. — just enough to exercise the
    VPN check branch.
    """
    from flask import Flask, jsonify, request
    from local_deep_research.security.vpn_precheck import (
        check_vpn_proxy, VPNCheckError,
    )

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/api/start_research", methods=["POST"])
    def fake_route():
        # Replicates the VPN-check branch only (not the full endpoint).
        proxy_enabled = True
        proxy_url = "http://172.25.128.1:10888"
        try:
            check_vpn_proxy(proxy_url)
        except VPNCheckError as e:
            return (
                jsonify({
                    "status": "error",
                    "error": "vpn_proxy_unavailable",
                    "message": str(e),
                    "hint": "Please enable your VPN proxy and try again.",
                }),
                422,
            )
        return jsonify({"status": "ok"}), 200

    return app.test_client()


def test_422_when_check_raises_vpn_check_error():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        side_effect=VPNCheckError_proxy_call(),
    ):
        # We patch the symbol imported into research_routes namespace.
        # Since the fake_route uses its own imported check_vpn_proxy, we
        # patch it in this module instead.
        with patch(
            "local_deep_research.security.vpn_precheck.check_vpn_proxy",
            side_effect=VPNCheckError("port unreachable: 1.2.3.4:10888 (refused)"),
        ):
            resp = client.post("/api/start_research", json={})
            assert resp.status_code == 422
            data = resp.get_json()
            assert data["error"] == "vpn_proxy_unavailable"
            assert "port unreachable" in data["message"]


def test_passthrough_when_check_succeeds():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        return_value=None,
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


def test_422_body_shape_matches_spec():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        side_effect=VPNCheckError("test failure"),
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 422
        data = resp.get_json()
        # All 4 spec-required keys present
        for key in ("status", "error", "message", "hint"):
            assert key in data, f"missing key: {key}"
        assert data["status"] == "error"
        assert data["error"] == "vpn_proxy_unavailable"
        assert "VPN proxy" in data["hint"] or "VPN" in data["hint"]
```

Note: the test references `VPNCheckError_proxy_call()` which is a leftover from an earlier draft — fix in next step.

- [ ] **Step 3: Fix test file (remove bogus helper call)**

In `tests/web/routes/test_start_research_vpn.py`, remove the line `side_effect=VPNCheckError_proxy_call(),` from the first `patch` block in `test_422_when_check_raises_vpn_check_error`. The outer `with patch(...)` already supplies a valid side_effect.

After fix, the first test reads:

```python
def test_422_when_check_raises_vpn_check_error():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        side_effect=VPNCheckError("port unreachable: 1.2.3.4:10888 (refused)"),
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["error"] == "vpn_proxy_unavailable"
        assert "port unreachable" in data["message"]
```

Add `from local_deep_research.security.vpn_precheck import VPNCheckError` to the top imports.

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker cp tests/web/routes/test_start_research_vpn.py ldr-local:/tmp/test_start_research_vpn.py
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/tmp')
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_start_research_vpn as mod
fns = [n for n in dir(mod) if n.startswith('test_')]
passed = failed = 0
for name in fns:
    try:
        getattr(mod, name)()
        print('PASS', name); passed += 1
    except Exception as e:
        print('FAIL', name, type(e).__name__, str(e)[:120]); failed += 1
print(f'{passed}/{passed+failed}')
sys.exit(failed)
"
```
Expected: 3 PASS, exit 0

- [ ] **Step 5: Insert VPN check into `start_research()` route**

In `src/local_deep_research/web/routes/research_routes.py`, find the `start_research()` function (starts at line 379 per project notes). After the line `data = request.json`, insert:

```python
    # VPN proxy reachability precheck (only when explicitly enabled).
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
                f"VPN precheck passed: user={current_user.username} "
                f"url={proxy_url}"
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

If `get_setting_from_snapshot` is not imported in this file, add the import at the top:

```python
from ..utilities.settings_utils import get_setting_from_snapshot
```

Verify after the insert: `grep -n "VPN precheck" src/local_deep_research/web/routes/research_routes.py` shows 1 match.

- [ ] **Step 6: Verify route file still parses**

Run:
```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
from local_deep_research.web.routes import research_routes
print('imported ok')
print('has start_research:', hasattr(research_routes, 'start_research'))
"
```
Expected: `imported ok` and `has start_research: True`

- [ ] **Step 7: Commit**

```bash
git add src/local_deep_research/web/routes/research_routes.py tests/web/routes/test_start_research_vpn.py
git commit -m "feat(routes): VPN precheck at /api/start_research entry"
```

---

## Task 5: Manual e2e verification + log sanity

**Files:** none (no code changes; this task is verification only)

- [ ] **Step 1: Re-run all new tests in one shot**

```bash
docker exec ldr-local python3 -c "
import sys
sys.path.insert(0, '/home/administrator/local-deep-research/src')
sys.path.insert(0, '/install/.venv/lib/python3.14/site-packages')
import test_vpn_precheck as a
import test_start_research_vpn as b
total_pass = total_fail = 0
for mod in [a, b]:
    fns = [n for n in dir(mod) if n.startswith('test_')]
    for name in fns:
        try:
            getattr(mod, name)(); print('PASS', mod.__name__, name); total_pass += 1
        except Exception as e:
            print('FAIL', mod.__name__, name, type(e).__name__, str(e)[:80]); total_fail += 1
print(f'TOTAL {total_pass}/{total_pass+total_fail}')
sys.exit(total_fail)
"
```
Expected: 14 PASS (11 from `test_vpn_precheck.py` + 3 from `test_start_research_vpn.py`), exit 0

- [ ] **Step 2: Restart container for hot-mount**

```bash
docker restart ldr-local
sleep 8
docker ps --filter name=ldr-local --format "table {{.Names}}\t{{.Status}}"
```
Expected: `ldr-local   Up X seconds (healthy)`

- [ ] **Step 3: Verify new code loaded in running worker**

```bash
docker exec ldr-local python3 -c "
import inspect
from local_deep_research.security import vpn_precheck
src = inspect.getsource(vpn_precheck.check_vpn_proxy)
print('has external_probe_url default:', 'google.com/generate_204' in src)
print('has ProxyHandler:', 'ProxyHandler' in src)
print('has HEAD method:', 'method=\"HEAD\"' in src)
"
```
Expected: all 3 True

- [ ] **Step 4: e2e scenario 1 — VPN enabled, proxy reachable (should pass)**

With WebUI's `app.network.proxy_enabled=true` and `app.network.proxy_url=http://172.25.128.1:10888` (current valid settings):

```bash
curl -s -X POST http://localhost:5000/api/start_research \
  -H "Content-Type: application/json" \
  -b /tmp/jar.txt -c /tmp/jar.txt \
  -d '{"query":"vpn test query","mode":"quick"}' \
  -o /tmp/r.json -w "%{http_code}\n"
```

Expected: HTTP 200 (or 302 redirect to login if no session — that's OK; the goal is to confirm 422 doesn't appear)

```bash
docker logs ldr-local --since 1m 2>&1 | grep "VPN precheck"
```
Expected: `VPN precheck passed: user=...` line present

- [ ] **Step 5: e2e scenario 2 — proxy URL points to closed port (should 422)**

Temporarily edit WebUI `app.network.proxy_url` to `http://127.0.0.1:1` (closed port). Re-trigger start_research.

Expected: HTTP 422 with body containing `"error": "vpn_proxy_unavailable"` and message `"VPN proxy port unreachable: 127.0.0.1:1 (...)"`.

```bash
docker logs ldr-local --since 1m 2>&1 | grep "VPN precheck"
```
Expected: `VPN precheck failed: user=... reason=VPN proxy port unreachable: ...` line present

- [ ] **Step 6: e2e scenario 3 — proxy_enabled=false (should skip check entirely)**

Restore `app.network.proxy_url` to `http://172.25.128.1:10888`, set `app.network.proxy_enabled=false`. Re-trigger.

Expected: HTTP 200, no `VPN precheck` log lines (check was skipped). This confirms the user's explicit "no proxy" choice is respected.

- [ ] **Step 7: Restore settings + final commit**

Restore `app.network.proxy_enabled=true` and the valid proxy URL. No code commit needed (this task is verification-only). If any debug logging was added during verification, revert it and commit as a separate `chore` commit.

---

## Self-Review

**1. Spec coverage**:
- Stdlib only ✓ (Tasks 1-3)
- 422 never 500 ✓ (Tasks 2, 3, 4)
- Read from `app.network.proxy_url` + `app.network.proxy_enabled` ✓ (Task 4)
- `proxy_enabled=false` skips ✓ (Task 4 + e2e Step 6)
- Per-step timeout 3s, total ≤ 6s ✓ (Tasks 2, 3)
- Default `https://www.google.com/generate_204` HEAD ✓ (Task 3)
- Stdlib not safe_get (comment in source) ✓ (Task 3)
- `showAlert` reuse via existing 422 else branch ✓ (verified in spec, no code change needed)
- Error handling matrix ✓ (covered by tests in Tasks 2, 3, 4)
- 5/3/4 test split for VPN unit tests, 3 route-level tests ✓
- ruff, hot-mount, commit conventions ✓ (each task ends with commit)

**2. Placeholder scan**: No "TBD"/"TODO"/"implement later" in any step. All code blocks are complete (no "..." ellipses). One known typo caught in Step 2 of Task 4 (`VPNCheckError_proxy_call()` bogus reference) — fixed in Step 3.

**3. Type consistency**: `check_vpn_proxy(proxy_url: str, *, external_probe_url: str = "...", timeout: float = 3.0) -> None` is used identically across Tasks 2, 3, 4, 5. `VPNCheckError` raised by both `_parse_proxy_url` and `check_vpn_proxy` consistently. Response body keys (`status`/`error`/`message`/`hint`) identical in Task 4 spec and Task 4 Step 3 test.

**No issues found — plan ready for execution.**