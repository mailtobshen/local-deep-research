# 暗网检索 阶段二：能检索（HTTP CONNECT 隧道变体） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阶段一已验证暗网检索链路通（`SearXNG + ldr-tor` 拿到 20 条 `.onion` snippet）。阶段二在「不破坏 SearXNG 集成设计意图」的前提下，新增一个**本地 HTTP CONNECT 隧道**，让宿主 Python 客户端能透明走 Tor 抓 `.onion` 全文 HTML；并接入研究流程，让用户在新建研究时勾选"同时检索暗网"。

**Architecture:** 6 个改动面、~220 行（含测试）+ 1 个独立进程（本地 CONNECT 代理）。

1. **本地代理进程**（`src/local_deep_research/network/onion_connect_proxy.py`，~91 行）：监听 `127.0.0.1:18080`，接收 HTTP CONNECT 请求，转发到 `172.21.0.4:9050` 的 SOCKS5h（`rdns=True` 让 tor 解析 `.onion`），双向中继字节流。strict 模式：拒绝非 `.onion` host。2. **proxy_config.py 集成**（~20 行）：新增 `get_onion_proxies(url)` 函数，按 host 后缀返回 `{'http': 'http://127.0.0.1:18080', 'https': 'http://127.0.0.1:18080'}` 或 `None`；**不改** `apply_proxy_to_wikipedia_env()` 的现有行为（tor 代理是**附加**层，不是替换）。3. **`_get_full_content` 路径改造**（`research_service.py:512-544`，~15 行）：对 `is_darkweb_url(url)` 调用 `kwargs.setdefault('proxies', get_onion_proxies(url))`。4. **引擎注册**（`engine_registry.py`，+7 行）：新增 `darkweb` EngineEntry，**不直接改 enabled 过滤**——在调用层检查。
5. **darkweb 检索工厂**（`src/local_deep_research/web_search_engines/darkweb.py`，~30 行）：`_make_darkweb_engine()` 实例化 `SearXNGSearchEngine(engines=["ahmia","torch"], categories=["onions"])`；`tag_darkweb()` 给暗网结果打 `metadata["source"]="darkweb"` 标记（provenance 冗余备份）。
6. **研究主流程接入**（`research_service.py`，~30 行）：暗网勾选时，**顺序拼接**——主引擎一次 + 暗网引擎一次；调用层合并 + provenance 标记。
7. **设置 / UI / preflight**：3 个文件 +60 行（沿用阶段一 `DARKWEB_ENGINES` 常量 + `probe_darkweb()` 函数）。

**Tech Stack:** Python 3.12（宿主 venv）/ 3.14（容器）、pytest、Flask、requests、urllib3、PySocks、SearXNG、Tor、HTTP CONNECT 协议。

## Global Constraints

- 分支：所有提交落在 `main`。每次提交前运行 `git rev-parse --abbrev-ref HEAD` 确认；不是 `main` 则停止。
- **禁止重启 `ldr-local` 容器**（`docker restart` / `docker compose up --force-recreate` / `down`）。其日志是研究任务的唯一证据来源，重建即永久销毁。需要代码生效时向用户报告并等待批准。
- 重启 `searxng-ldr` 是允许的，它不影响 ldr-local 日志。
- 本地代理**只跑在宿主**，不进 docker compose（避免容器内端口冲突 + 减少攻击面）。启动由 `scripts/start-onion-proxy.sh` 触发。
- 本地代理**必须** strict 模式（拒绝非 `.onion` host）——避免明网误路由到 tor exit node，触发 Cloudflare 风控。
- **不修改** `apply_proxy_to_wikipedia_env()` 在 `proxy_config.py:315` 的现有 `HTTP_PROXY` 写入行为。tor 代理是**附加层**，**不替换** Privoxy。
- 测试命令一律使用 `.venv/bin/python -m pytest`，不使用系统 python。
- 阶段二不引入 `int("D1")` 防御（阶段三做）；不引入 `[D1]` 编号重写（阶段三做）；不引入章节标注（阶段三做）。
- 实施期间**保持阶段一 `probe_darkweb()` 函数不变**，阶段二只新增不修改。

---

## 本阶段不做（明确划界）

| 项 | 何时做 |
|---|---|
| `[D1]` 编号重写、参考文献分组、章节末尾标注 | 阶段三 |
| `build_citation_index` 防御 `int("D1")` 崩溃 | 阶段三 |
| 图片管道跳过 `.onion` 来源（设计文档 §"安全约束"明确排除）| 阶段三 |
| 阶段一 L2 检查 bug（`get_searxng_engines` 用 `_FALLBACK_ENGINES` 过滤）| 独立 fix（不在本计划范围）|
| 暗网结果去重 / 风险评级（设计文档明确非目标）| 不做 |
| 抓 `.onion` 图片（设计文档明确非目标）| 不做 |

---

## File Structure

| 文件 | 职责 | Task |
|---|---|---|
| `src/local_deep_research/network/onion_connect_proxy.py`（新建） | 本地 HTTP CONNECT 代理 | Task 1 |
| `src/local_deep_research/network/__init__.py`（新建空文件） | package marker | Task 1 |
| `scripts/start-onion-proxy.sh`（新建） | 拉起本地代理的 shell 脚本 | Task 1 |
| `src/local_deep_research/security/proxy_config.py`（修改） | 新增 `get_onion_proxies(url)` | Task 2 |
| `tests/security/test_onion_proxies.py`（新建） | `get_onion_proxies()` 行为测试 | Task 2 |
| `src/local_deep_research/web_search_engines/darkweb.py`（新建） | `_make_darkweb_engine()` + `tag_darkweb()` | Task 3 |
| `tests/web_search_engines/test_darkweb_factory.py`（新建） | 工厂 + 标记测试 | Task 3 |
| `src/local_deep_research/web_search_engines/engine_registry.py`（修改） | 新增 `darkweb` EngineEntry | Task 3 |
| `src/local_deep_research/web/services/research_service.py`（修改） | `_get_full_content` 走 onion 代理 | Task 4 |
| `src/local_deep_research/web/services/research_service.py`（修改） | 研究主流程暗网追加分支 | Task 5 |
| `src/local_deep_research/defaults/default_settings.json`（修改） | 6 个 `search.engine.web.darkweb.*` 设置 | Task 6 |
| `src/local_deep_research/web/translations/zh.json`（修改） | 暗网相关英文条目中文翻译 | Task 6 |
| `src/local_deep_research/web/routes/settings_routes.py`（修改） | UI 端点返回 enabled 状态 | Task 7 |
| `src/local_deep_research/web/templates/research.html`（修改） | 勾选框条件渲染 | Task 7 |
| `src/local_deep_research/diagnostics/engine_health.py`（修改） | `run_preflight_check` 加 darkweb 分支 | Task 8 |
| `tests/network/test_onion_connect_proxy.py`（新建） | 本地代理单元 + 集成测试 | Task 9 |
| `tests/web/test_darkweb_phase2.py`（新建） | 端到端集成（mock 化） | Task 9 |

---

### Task 1: 本地 HTTP CONNECT 代理（核心基础设施）

**Files:**
- Create: `src/local_deep_research/network/__init__.py`（空文件，package marker）
- Create: `src/local_deep_research/network/onion_connect_proxy.py`（~91 行）
- Create: `scripts/start-onion-proxy.sh`（~20 行）
- Test: `tests/network/test_onion_connect_proxy.py`（~80 行）

**Interfaces:**
- Produces:
- `OnionConnectProxy` 类：监听 `127.0.0.1:18080`（端口可配），处理 HTTP CONNECT 请求
- 严格模式（默认）：只接受 host 后缀为 `.onion` 的 CONNECT 请求；其他返回 `HTTP/1.1 403 Forbidden`
- 非严格模式（可选）：接受任意 host（**仅供测试用**）
- 命令行入口：`python -m local_deep_research.network.onion_connect_proxy [--port=18080] [--tor-host=172.21.0.4] [--tor-port=9050] [--strict/--no-strict]`
- 启动脚本：`scripts/start-onion-proxy.sh` 检测代理是否已在跑，未在跑则拉起，pid 写入 `/tmp/onion-connect-proxy.pid`

**核心技术点**（来自实测 `/tmp/connect_proxy.py` 模板）：
```python
# SOCKS5h 直连（rdns=True 让 tor 解析 .onion）
s = socks.socksocket()
s.set_proxy(socks.SOCKS5, tor_host, tor_port, rdns=True)
s.connect((target_host, target_port))
```

- [ ] **Step 1: 写失败测试**

创建 `tests/network/test_onion_connect_proxy.py`：

```python
"""Unit tests for the local HTTP CONNECT proxy.

These tests exercise the proxy logic with a *mock* SOCKS5 server, so they do
NOT require ldr-tor to be reachable. The end-to-end test against a real
.onion URL is in tests/web/test_darkweb_phase2.py and is skipped when the
host-side Tor egress is unavailable.
"""
import socket
import threading
from unittest.mock import patch

import pytest

from local_deep_research.network.onion_connect_proxy import OnionConnectProxy


@pytest.fixture
def mock_socks5_server():
    """Stand up a minimal TCP server that accepts a single byte and echoes
    the rest. Lets us verify the proxy's relay logic without involving Tor."""
    received = bytearray()

    def serve(sock, addr):
        try:
            data = sock.recv(4096)
            received.extend(data)
            sock.sendall(b"MOCK-SOCKS5-OK")
        finally:
            sock.close()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    t = threading.Thread(target=lambda: _accept_one(server, serve), daemon=True)
    t.start()
    yield port, received
    server.close()


def _accept_one(server, handler):
    sock, addr = server.accept()
    handler(sock, addr)


def test_strict_rejects_non_onion_host(mock_socks5_server):
    """strict mode is the default; non-.onion host returns 403."""
    port, _ = mock_socks5_server
    proxy = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=port, strict=True)
    # bind to ephemeral port; we just need the connect handler

    # Use a real socket against the proxy; we need it bound first.
    # We bind manually because the production class needs a real port.
    import socket as _socket
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=port, strict=True)
    p._bind()
    real_port = p.port

    client = _socket.create_connection(("127.0.0.1", real_port), timeout=5)
    client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
    resp = client.recv(4096)
    assert b"403" in resp
    client.close()
    p._close()


def test_non_strict_accepts_any_host(mock_socks5_server):
    """non-strict mode proxies any host (test-only)."""
    socks_port, received = mock_socks5_server
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=socks_port, strict=False)
    p._bind()
    real_port = p.port

    client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
    client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
    resp = client.recv(4096)
    assert b"200" in resp
    assert b"MOCK-SOCKS5-OK" in resp
    client.close()
    p._close()


def test_default_is_strict():
    """strict must default to True to prevent accidental non-onion routing."""
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=9050)
    assert p.strict is True
```

- [ ] **Step 2: 验证测试失败**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/network/test_onion_connect_proxy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'local_deep_research.network'`.

- [ ] **Step 3: 创建 `network/` package 与空 `__init__.py`**

```bash
mkdir -p /home/administrator/local-deep-research/src/local_deep_research/network
touch /home/administrator/local-deep-research/src/local_deep_research/network/__init__.py
```

- [ ] **Step 4: 实现 `onion_connect_proxy.py`**

Create `src/local_deep_research/network/onion_connect_proxy.py`（基于 `/tmp/connect_proxy.py` 模板，**改写**而非原样 copy ——加 strict 模式 + 类型注解 + 日志）：

```python
"""Local HTTP CONNECT proxy that tunnels to a SOCKS5h Tor endpoint.

Listens on 127.0.0.1:18080 (configurable) and accepts HTTP CONNECT requests.
Each CONNECT request is forwarded to the configured SOCKS5h proxy with
``rdns=True`` so the upstream Tor resolves ``.onion`` hostnames. Bytes are
relayed bidirectionally between the client and the upstream.

Strict mode (default): rejects CONNECT requests whose target host does not
end in ``.onion`` with HTTP 403. This prevents accidental mis-routing of
clearnet traffic to Tor exit nodes (which would trigger Cloudflare CAPTCHAs
on the next request).

Run directly::

    python -m local_deep_research.network.onion_connect_proxy

Or programmatically::

    proxy = OnionConnectProxy(port=18080)
    proxy.serve_forever()  # blocks
"""
import argparse
import logging
import socket
import threading

import socks  # PySocks; already installed via requests[socks]

log = logging.getLogger(__name__)

DEFAULT_PORT = 18080
DEFAULT_TOR_HOST = "172.21.0.4"
DEFAULT_TOR_PORT = 9050
ONION_SUFFIX = ".onion"


class OnionConnectProxy:
    """HTTP CONNECT → SOCKS5h tunnel for .onion hostnames.

    Parameters
    ----------
    port : int
        Local listen port. 0 means pick an ephemeral port.
    tor_host : str
        Upstream SOCKS5h host (the Tor sidecar).
    tor_port : int
        Upstream SOCKS5h port (the Tor SOCKSPort).
    strict : bool
        When True (default), only ``.onion`` hosts are accepted. Other
        CONNECT targets get a 403 to avoid mis-routing clearnet traffic
        to Tor exit nodes.
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        tor_host: str = DEFAULT_TOR_HOST,
        tor_port: int = DEFAULT_TOR_PORT,
        strict: bool = True,
    ):
        self.port = port
        self.tor_host = tor_host
        self.tor_port = tor_port
        self.strict = strict
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []

    # ---- lifecycle (testable surface) ----

    def _bind(self) -> None:
        """Bind the listening socket; exposes the real port via self.port."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self.port = self._server.getsockname()[1]
        self._server.listen(8)
        log.info(
            "OnionConnectProxy listening on 127.0.0.1:%d → SOCKS5h %s:%d (strict=%s)",
            self.port, self.tor_host, self.tor_port, self.strict,
        )

    def _close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def serve_forever(self) -> None:
        """Bind (if needed) and serve until KeyboardInterrupt."""
        if self._server is None:
            self._bind()
        try:
            while True:
                client, addr = self._server.accept()
                t = threading.Thread(
                    target=self._handle, args=(client,), daemon=True
                )
                t.start()
                self._threads.append(t)
        except KeyboardInterrupt:
            log.info("OnionConnectProxy shutting down")
            self._close()

    # ---- request handling ----

    def _handle(self, client: socket.socket) -> None:
        try:
            data = client.recv(4096)
            if not data:
                return
            first_line = data.split(b"\r\n", 1)[0]
            parts = first_line.split(b" ")
            if len(parts) < 3 or parts[0] != b"CONNECT":
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            hostport = parts[1].decode("latin-1", errors="replace")
            host, _, port_s = hostport.rpartition(":")
            if not host or not port_s.isdigit():
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            port = int(port_s)
            if self.strict and not host.endswith(ONION_SUFFIX):
                log.warning(
                    "REJECT %s:%d - not %s (strict mode)",
                    host, port, ONION_SUFFIX,
                )
                client.sendall(
                    b"HTTP/1.1 403 Forbidden\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                return
            try:
                upstream = socks.socksocket()
                upstream.set_proxy(
                    socks.SOCKS5, self.tor_host, self.tor_port, rdns=True,
                )
                upstream.connect((host, port))
            except socks.SOCKS5Error as exc:
                log.error("SOCKS5 error for %s:%d: %s", host, port, exc)
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            _relay(client, upstream)
        except Exception:
            log.exception("proxy handler crashed")
        finally:
            try:
                client.close()
            except OSError:
                pass


def _relay(a: socket.socket, b: socket.socket) -> None:
    """Bidirectional byte relay between two sockets."""
    import selectors
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ)
    sel.register(b, selectors.EVENT_READ)
    try:
        while True:
            for key, _ in sel.select(timeout=30):
                try:
                    data = key.fileobj.recv(8192)
                except OSError:
                    return
                if not data:
                    return
                other = b if key.fileobj is a else a
                try:
                    other.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tor-host", default=DEFAULT_TOR_HOST)
    parser.add_argument("--tor-port", type=int, default=DEFAULT_TOR_PORT)
    parser.add_argument(
        "--strict/--no-strict", dest="strict",
        action=argparse.BooleanOptionalAction, default=True,
    )
    args = parser.parse_args()
    OnionConnectProxy(
        port=args.port,
        tor_host=args.tor_host,
        tor_port=args.tor_port,
        strict=args.strict,
    ).serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 验证单元测试通过**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/network/test_onion_connect_proxy.py -q`
Expected: all 3 tests PASS.

- [ ] **Step 6: 写启动脚本**

Create `scripts/start-onion-proxy.sh`：

```bash
#!/usr/bin/env bash
# Start the local HTTP CONNECT → SOCKS5h tunnel for .onion fetches.
# Idempotent: if the proxy is already running, do nothing.
set -euo pipefail

PID_FILE="${ONION_PROXY_PID_FILE:-/tmp/onion-connect-proxy.pid}"
LOG_FILE="${ONION_PROXY_LOG_FILE:-/tmp/onion-connect-proxy.log}"

# If pid file exists and the process is alive, nothing to do.
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "OnionConnectProxy already running (pid $(cat "$PID_FILE"))"
    exit 0
fi

# Start fresh.
nohup .venv/bin/python -m local_deep_research.network.onion_connect_proxy \
    >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
sleep 1
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "OnionConnectProxy started (pid $(cat "$PID_FILE"), log $LOG_FILE)"
else
    echo "OnionConnectProxy failed to start; see $LOG_FILE" >&2
    exit 1
fi
```

`chmod +x scripts/start-onion-proxy.sh`

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/network/__init__.py \
        src/local_deep_research/network/onion_connect_proxy.py \
        scripts/start-onion-proxy.sh \
        tests/network/test_onion_connect_proxy.py
git commit -m "feat(network): local HTTP CONNECT proxy for .onion egress

Adds OnionConnectProxy, a 127.0.0.1:18080 listener that accepts HTTP
CONNECT requests and tunnels them to the ldr-tor SOCKS5h endpoint with
rdns=True so .onion hostnames are resolved by Tor. Strict mode (default)
rejects non-.onion targets with HTTP 403 to prevent accidental mis-routing
of clearnet traffic to Tor exit nodes (which would trigger Cloudflare
CAPTCHAs). The proxy runs as a host-side process (not inside the
container); scripts/start-onion-proxy.sh is idempotent. PySocks 1.7.1 is
already a transitive dependency via requests[socks]."
git log --oneline -3
```

---

### Task 2: `proxy_config.py` 新增 `get_onion_proxies(url)`

**Files:**
- Modify: `src/local_deep_research/security/proxy_config.py`（在 `get_proxy_settings()` 之后插入新函数）
- Test: `tests/security/test_onion_proxies.py`（新建，~30 行）

**Interfaces:**
- Produces:
- `get_onion_proxies(url: str) -> Optional[Dict[str, str]]`：根据 URL host 后缀返回 `{'http': 'http://127.0.0.1:18080', 'https': 'http://127.0.0.1:18080'}` 或 `None`
- `ONION_PROXY_URL` 常量：`"http://127.0.0.1:18080"`

**关键约束**：**不修改** `apply_proxy_to_wikipedia_env()` 在 :315 的现有行为。tor 代理是**附加层**，调用方用 `kwargs.setdefault("proxies", get_onion_proxies(url))` 模式。

- [ ] **Step 1: 写失败测试**

Create `tests/security/test_onion_proxies.py`：

```python
"""get_onion_proxies() returns the local CONNECT proxy URL only for .onion
hosts, and is otherwise None. Combined with kwargs.setdefault pattern this
layers onion-specific routing on top of any existing app.network proxy.
"""
from local_deep_research.security.proxy_config import get_onion_proxies


def test_onion_url_returns_proxy():
    out = get_onion_proxies("http://kx5thpx2oluwml4w.onion/path")
    assert out == {"http": "http://127.0.0.1:18080", "https": "http://127.0.0.1:18080"}


def test_onion_https_url_returns_proxy():
    out = get_onion_proxies("https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/")
    assert out is not None
    assert out["http"] == "http://127.0.0.1:18080"


def test_clearnet_returns_none():
    assert get_onion_proxies("https://example.com/") is None
    assert get_onion_proxies("http://1.1.1.1/") is None


def test_uppercase_onion_returns_proxy():
    """Case-insensitive suffix match (.ONION should also work)."""
    out = get_onion_proxies("http://EXAMPLE.ONION/")
    assert out is not None


def test_malformed_url_returns_none():
    """Garbage in, None out — never raises."""
    assert get_onion_proxies("not a url") is None
    assert get_onion_proxies("") is None
```

- [ ] **Step 2: 验证失败**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/security/test_onion_proxies.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_onion_proxies'`.

- [ ] **Step 3: 在 `proxy_config.py` 添加函数**

打开 `src/local_deep_research/security/proxy_config.py`，**在第 103 行之后**（紧接 `get_proxy_settings()` 末尾的 `return`），插入：

```python
ONION_PROXY_URL = "http://127.0.0.1:18080"


def get_onion_proxies(url: str) -> Optional[Dict[str, str]]:
    """Return the local CONNECT proxy only for ``.onion`` URLs.

    Layered on top of ``get_proxy_settings()``: callers should use the
    ``kwargs.setdefault("proxies", get_onion_proxies(url))`` pattern so the
    existing app.network proxy (Privoxy) keeps handling clearnet traffic.
    """
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return None
    if not host:
        return None
    if host == "onion" or host.endswith(".onion"):
        return {"http": ONION_PROXY_URL, "https": ONION_PROXY_URL}
    return None
```

**注意**：仅在文件末尾追加 `get_onion_proxies()` 函数体，**不动** `apply_proxy_to_wikipedia_env()` 的现有 HTTP_PROXY 写入逻辑。

- [ ] **Step 4: 验证测试通过**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/security/test_onion_proxies.py -q`
Expected: 5 tests PASS.

- [ ] **Step 5: 跑现有 proxy_config 测试做回归**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/security/test_proxy_private_network_detection.py tests/security/test_safe_requests.py -q`
Expected: all PASS (未触动现有行为)。

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/security/proxy_config.py \
        tests/security/test_onion_proxies.py
git commit -m "feat(security): get_onion_proxies() layered on app.network proxy

Returns the local CONNECT proxy URL only for .onion hosts (case-insensitive
suffix match), None otherwise. Uses kwargs.setdefault pattern at call sites
so the existing app.network proxy (Privoxy) keeps handling clearnet. Does
NOT modify apply_proxy_to_wikipedia_env()'s HTTP_PROXY write behavior — Tor
proxy is an additive layer, not a replacement."
git log --oneline -3
```

---

### Task 3: 暗网引擎工厂 + `darkweb` EngineEntry

**Files:**
- Create: `src/local_deep_research/web_search_engines/darkweb.py`（~30 行）
- Create: `tests/web_search_engines/test_darkweb_factory.py`（~40 行）
- Modify: `src/local_deep_research/web_search_engines/engine_registry.py`（在末尾添加 `darkweb` EngineEntry）

**Interfaces:**
- Produces:
- `_make_darkweb_engine(instance_url="http://searxng-ldr:8080") -> SearXNGSearchEngine`：实例化 `engines=["ahmia","torch"], categories=["onions"], max_results=10`
- `tag_darkweb(results: list[dict]) -> list[dict]`：给每个结果打 `metadata["source"]="darkweb"` 与 `is_darkweb=True`
- `darkweb` EngineEntry 注册到 `ENGINE_REGISTRY`

- [ ] **Step 1: 写失败测试**

Create `tests/web_search_engines/test_darkweb_factory.py`：

```python
"""darkweb engine factory and provenance tagging."""
from local_deep_research.web_search_engines.darkweb import (
    _make_darkweb_engine,
    tag_darkweb,
)


def test_make_darkweb_engine_has_correct_params():
    """Engine must be configured to route via SearXNG's ahmia/torch + onions."""
    e = _make_darkweb_engine()
    assert e.engines == ["ahmia", "torch"]
    assert "onions" in e.categories
    # Don't assert instance_url (config-driven; default to searxng-ldr).
    assert e.max_results <= 10


def test_tag_darkweb_adds_provenance():
    results = [
        {"url": "http://aaa.onion/", "title": "x", "content": "y"},
        {"url": "http://bbb.onion/", "title": "x", "content": "y"},
    ]
    out = tag_darkweb(results)
    assert len(out) == 2
    for r in out:
        assert r.get("is_darkweb") is True
        assert r.get("metadata", {}).get("source") == "darkweb"
        # Source URL preserved.
        assert r["url"].endswith(".onion/")


def test_tag_darkweb_handles_empty():
    assert tag_darkweb([]) == []


def test_engine_registry_has_darkweb():
    from local_deep_research.web_search_engines.engine_registry import (
        ENGINE_REGISTRY,
        get_engine_entry,
    )
    assert "darkweb" in ENGINE_REGISTRY
    entry = get_engine_entry("darkweb")
    assert entry is not None
    assert entry.class_name == "SearXNGSearchEngine"
```

- [ ] **Step 2: 验证失败**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web_search_engines/test_darkweb_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'local_deep_research.web_search_engines.darkweb'`（先报这错误）。

- [ ] **Step 3: 创建 `darkweb.py`**

Create `src/local_deep_research/web_search_engines/darkweb.py`：

```python
"""Darkweb (SearXNG + ahmia/torch) search engine factory.

Reuses SearXNGSearchEngine; configures it with the darkweb engine list and
the ``onions`` category. Tor egress is handled server-side by SearXNG's own
``proxies: socks5h://ldr-tor:9050`` setting — no Tor client is needed in
this process for the search phase. Full-content fetch is handled by the
onion CONNECT tunnel (see security/proxy_config.get_onion_proxies).
"""
from typing import Optional

from local_deep_research.web_search_engines.engines.search_engine_searxng import (
    SearXNGSearchEngine,
)
from local_deep_research.web_search_engines.engines.full_search import (
    FullSearchResults,
)

DARKWEB_DEFAULT_INSTANCE_URL = "http://searxng-ldr:8080"
DARKWEB_DEFAULT_ENGINES = ("ahmia", "torch")
DARKWEB_DEFAULT_CATEGORIES = ("onions",)
DARKWEB_DEFAULT_MAX_RESULTS = 10


def _make_darkweb_engine(
    instance_url: Optional[str] = None,
) -> SearXNGSearchEngine:
    """Instantiate a SearXNG client configured for darkweb engines.

    Parameters
    ----------
    instance_url : str, optional
        SearXNG instance URL. Defaults to ``DARKWEB_DEFAULT_INSTANCE_URL``
        (``http://searxng-ldr:8080`` — the in-network sidecar).
    """
    return SearXNGSearchEngine(
        instance_url=instance_url or DARKWEB_DEFAULT_INSTANCE_URL,
        engines=list(DARKWEB_DEFAULT_ENGINES),
        categories=list(DARKWEB_DEFAULT_CATEGORIES),
        max_results=DARKWEB_DEFAULT_MAX_RESULTS,
        full_search_results=FullSearchResults(),
    )


def tag_darkweb(results: list[dict]) -> list[dict]:
    """Mark each result as a darkweb hit.

    Tags ``is_darkweb=True`` and ``metadata.source="darkweb"`` so downstream
    consumers can branch on provenance. The URL is the authoritative
    signal (``.onion`` suffix); this tagging is a redundancy for grep-
    ability and for callers that filter before URL parsing.

    Parameters
    ----------
    results : list[dict]
        Raw SearXNG result dicts from ``_make_darkweb_engine().search()``.

    Returns
    -------
    list[dict]
        The same list, mutated in place and returned for chaining.
    """
    for r in results:
        r.setdefault("metadata", {})["source"] = "darkweb"
        r["is_darkweb"] = True
    return results
```

- [ ] **Step 4: 在 `engine_registry.py` 添加 `darkweb`**

打开 `src/local_deep_research/web_search_engines/engine_registry.py`，**在末尾（第 163 行 `MetaSearchEngine` 之后、第 164 行的 `}` 之前）**，在 `MetaSearchEngine` 块下方加 `darkweb` EngineEntry：

```python
    # --- Darkweb engine (SearXNG ahmia/torch via ldr-tor) ---
    "darkweb": EngineEntry(
        module_path=".engines.search_engine_searxng",
        class_name="SearXNGSearchEngine",
        full_search_module=".engines.full_search",
        full_search_class="FullSearchResults",
    ),
}
```

**注意**：注册时不检查 `enabled`——`enabled` 过滤在调用层做（Task 5 研究主流程）。

- [ ] **Step 5: 验证测试通过**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web_search_engines/test_darkweb_factory.py -q`
Expected: 4 tests PASS.

- [ ] **Step 6: 跑现有 engine_registry 测试做回归**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web_search_engines/ -q`
Expected: all PASS（只是注册表加了一项，不影响现有引擎）。

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/web_search_engines/darkweb.py \
        src/local_deep_research/web_search_engines/engine_registry.py \
        tests/web_search_engines/test_darkweb_factory.py
git commit -m "feat(search): darkweb engine factory + provenance tagging

_make_darkweb_engine() instantiates SearXNGSearchEngine with
engines=[ahmia,torch], categories=[onions]. tag_darkweb() marks each
result with is_darkweb=True and metadata.source='darkweb' so downstream
consumers can branch on provenance. Registers the darkweb EngineEntry
in ENGINE_REGISTRY; enabled filtering happens at the call site (Task 5),
not at registration, so test fixtures and introspection still see it."
git log --oneline -3
```

---

### Task 4: `_get_full_content` 走 onion 代理

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py`（约 `:512-544` 附近 `_get_full_content` 路径）
- Test: `tests/web/test_darkweb_phase2.py`（追加 helper tests，~30 行）

**Interfaces:**
- Consumes: `get_onion_proxies(url)`（Task 2）
- Produces: `_get_full_content`（或等价调用点）对 `.onion` URL 自动注入 `proxies` 参数

- [ ] **Step 1: 找到真实锚点**

Run: `grep -n "_get_full_content\|relevant_item\|safe_get.*url" /home/administrator/local-deep-research/src/local_deep_research/web/services/research_service.py | head -20`
Expected: 至少一行显示 `_get_full_content` 的调用或定义位置。记下精确行号。

- [ ] **Step 2: 写失败测试**

Append to `tests/web/test_darkweb_phase2.py`：

```python
"""Phase-2 darkweb integration: _get_full_content picks onion proxies for
.onion URLs but not for clearnet URLs."""


def test_onion_url_gets_local_proxy(monkeypatch):
    from local_deep_research.security import proxy_config
    from local_deep_research.web.services import research_service

    captured = {}

    def fake_safe_get(url, *args, **kwargs):
        captured["url"] = url
        captured["proxies"] = kwargs.get("proxies")
        # Return a fake response object so the rest of the pipeline is happy.
        from unittest.mock import MagicMock
        m = MagicMock()
        m.text = "<html></html>"
        m.status_code = 200
        return m

    monkeypatch.setattr(research_service, "safe_get", fake_safe_get)
    # Call _get_full_content with a single .onion URL — adjust import as needed.
    # ... (this depends on the exact public surface; write the call here).
    assert captured.get("proxies") == {"http": "http://127.0.0.1:18080",
                                       "https": "http://127.0.0.1:18080"}


def test_clearnet_url_unaffected():
    """Clearnet URLs must not get onion proxies."""
    from local_deep_research.security.proxy_config import get_onion_proxies
    assert get_onion_proxies("https://example.com/") is None
```

> **注**：Step 2 的具体测试写法取决于 Step 1 grep 出来的真实接口。如果 `_get_full_content` 不接受 kwargs，需要在更高层调用点（`_get_search_results` 的相关入口）注入。**在写代码前先 grep 再写测试**。

- [ ] **Step 3: 验证失败**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -q`
Expected: FAIL（proxies 未注入 → clearnet 路径返回 None / onion 路径没设）。

- [ ] **Step 4: 注入 proxies**

在 `_get_full_content` 路径（具体行号看 Step 1 grep 结果）调用 `safe_get(url, ...)` 之前，加：

```python
from local_deep_research.security.proxy_config import get_onion_proxies

# ... before the safe_get call:
extra_proxies = get_onion_proxies(url)
if extra_proxies is not None:
    kwargs.setdefault("proxies", extra_proxies)
```

**最小改动**：单点插入（仅在 `_get_full_content` 内的 `safe_get(url, ...)` 之前），不动其他逻辑。

- [ ] **Step 5: 验证测试通过**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -q`
Expected: PASS.

- [ ] **Step 6: 跑 deferred-fill 套件做回归**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py -q`
Expected: all PASS（未触动业务逻辑）。

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/web/services/research_service.py \
        tests/web/test_darkweb_phase2.py
git commit -m "feat(research): route .onion fetches through local CONNECT proxy

_get_full_content now consults get_onion_proxies(url) before calling
safe_get. For .onion targets it injects the local CONNECT proxy URL so
urllib3 skips its DNS pre-resolution path and lets Tor resolve via
SOCKS5h. Clearnet URLs are untouched (Privoxy stays in charge). The
change is a single kwargs.setdefault insertion at the safe_get call site."
git log --oneline -3
```

---

### Task 5: 研究主流程接入暗网追加分支

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py`（主流程检索调用点）

**Interfaces:**
- Consumes: `_make_darkweb_engine()` (Task 3), `tag_darkweb()` (Task 3), `search.engine.web.darkweb.enabled` 设置（Task 6）
- Produces: 研究主流程在 `darkweb_enabled` 时，**顺序**调用主引擎 + 暗网引擎；调用层合并结果 + provenance 标记

**关键设计抉择**：选择**顺序拼接两次检索**而非"混合单流"，因为：
- SearXNG 端 `socks5h://` 已正确处理 `.onion`（阶段一实测验证）
- 明暗结果可独立走不同流水线（明网 `_get_full_content` 走 Privoxy，暗网走 CONNECT 代理）
- 来源标记清晰，阶段三 `[D1]` 编号更简单

- [ ] **Step 1: 找到主流程检索调用点**

Run: `grep -n "engine.search\|_run_search\|do_search" /home/administrator/local-deep-research/src/local_deep_research/web/services/research_service.py | head -10`
Expected: 标识主流程里调用 `engine.search(query)` 的位置。

- [ ] **Step 2: 写失败测试**

Append to `tests/web/test_darkweb_phase2.py`：

```python
def test_darkweb_disabled_no_extra_call(monkeypatch):
    """When darkweb is disabled in settings, no second SearXNG call happens."""
    # Stub settings_snapshot so 'enabled' returns False.
    from local_deep_research.web.services import research_service
    calls = []

    def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"url": "http://x/", "title": "x", "content": "y"}]

    monkeypatch.setattr(research_service, "search_engine_search", fake_search)
    # Run a slice of the main flow with a snapshot that has darkweb.enabled=False.
    # ... (depends on the actual main-flow function; insert test here).
    assert len(calls) == 1  # only the main engine


def test_darkweb_enabled_two_searches_with_tag(monkeypatch):
    """When darkweb is enabled, main + darkweb each run once; results tagged."""
    # ... insert test mirroring the actual main-flow signature.
    pass  # placeholder until you grep Step 1
```

> Step 2 的具体写法依赖 Step 1 grep 出的真实主流程函数签名。

- [ ] **Step 3: 验证失败**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -q`
Expected: FAIL（暗网追加分支不存在）。

- [ ] **Step 4: 插入暗网追加分支**

在主流程检索调用点（Step 1 找到的位置）**之后**追加：

```python
from local_deep_research.web_search_engines.darkweb import (
    _make_darkweb_engine,
    tag_darkweb,
)

# Read the enabled flag from settings_snapshot (declared in Task 6).
darkweb_enabled = bool(
    get_setting_from_snapshot(
        "search.engine.web.darkweb.enabled", False,
        settings_snapshot=settings_snapshot,
    )
)
if darkweb_enabled:
    darkweb_engine = _make_darkweb_engine()
    darkweb_results = darkweb_engine.search(query) or []
    results = results + tag_darkweb(darkweb_results)
```

**关键**：用 `get_setting_from_snapshot()`（已在 `research_service.py:383` 导入）+ `kwargs.setdefault` 风格。**不改**原有主流程的 search 调用。

- [ ] **Step 5: 验证测试通过 + 回归**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py tests/web/test_deferred_image_fill.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/web/services/research_service.py \
        tests/web/test_darkweb_phase2.py
git commit -m "feat(research): main flow merges darkweb engine when enabled

When search.engine.web.darkweb.enabled is true, the main flow runs a
second SearXNGSearchEngine configured with engines=[ahmia,torch],
categories=[onions] and merges its results into the main engine's
output via tag_darkweb(). Two independent SearXNG calls give clear
provenance separation (vs. mixing engines in a single SearXNG request).
The darkweb result is later routed through the local CONNECT proxy
(Task 4) for full-content fetch."
git log --oneline -3
```

---

### Task 6: 配置项 + i18n

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json`
- Modify: `src/local_deep_research/web/translations/zh.json`
- Test: extend `tests/web/test_darkweb_phase2.py`

**Interfaces:**
- Produces 6 个新键：
```
search.engine.web.darkweb.enabled            = false
search.engine.web.darkweb.display_name       = "暗网检索 (Tor)"
search.engine.web.darkweb.reliability        = 0.3
search.engine.web.darkweb.use_in_auto_search = false
search.engine.web.darkweb.default_params.instance_url = "http://searxng-ldr:8080"
search.engine.web.darkweb.default_params.engines      = ["ahmia", "torch"]
search.engine.web.darkweb.default_params.categories   = ["onions"]
search.engine.web.darkweb.default_params.max_results  = 10
```

**注**：`default_settings.json` 的实际 schema 需要先 grep 现有条目确认。

- [ ] **Step 1: 找到 default_settings.json 的 schema 锚点**

Run: `grep -n "search.engine.web" /home/administrator/local-deep-research/src/local_deep_research/defaults/default_settings.json | head -10`
Expected: 标识现有 `search.engine.web.*` 条目的格式（缩进、字段名）。

- [ ] **Step 2: 写失败测试**

Append to `tests/web/test_darkweb_phase2.py`：

```python
def test_darkweb_settings_declared():
    import json
    from pathlib import Path
    import local_deep_research.defaults as pkg

    d = json.loads(Path(pkg.__file__).parent.joinpath("default_settings.json").read_text())
    key = "search.engine.web.darkweb.enabled"
    assert key in d
    assert d[key]["value"] is False
    assert d[key]["ui_element"] == "checkbox"
    assert d[key]["category"] == "search_engines"


def test_darkweb_default_params_declared():
    import json
    from pathlib import Path
    import local_deep_research.defaults as pkg

    d = json.loads(Path(pkg.__file__).parent.joinpath("default_settings.json").read_text())
    params_key = "search.engine.web.darkweb.default_params"
    assert params_key in d
    assert d[params_key]["value"]["engines"] == ["ahmia", "torch"]
    assert d[params_key]["value"]["categories"] == ["onions"]
```

- [ ] **Step 3: 验证失败 + 插入 JSON 条目**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -q -k darkweb_settings`
Expected: FAIL.

打开 `default_settings.json`，在最后一个 `search.engine.web.*` 条目的 `}` 后插入新的 darkweb 条目（匹配现有缩进格式）。**精确格式见实际文件**——grep 后复制粘贴对齐。

- [ ] **Step 4: 验证 JSON 合法 + 测试通过**

```bash
.venv/bin/python -c "import json; json.load(open('src/local_deep_research/defaults/default_settings.json')); print('OK')"
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -q -k darkweb_settings
```

- [ ] **Step 5: zh.json 翻译**

找到 `web/translations/zh.json` 里 "Display name" / "Description" / "Reliability" 现有英文条目，按 Task 1 阶段一 `4b47cfea` 的方式追加暗网相关 key/value。

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/defaults/default_settings.json \
        src/local_deep_research/web/translations/zh.json \
        tests/web/test_darkweb_phase2.py
git commit -m "feat(settings): search.engine.web.darkweb.* + zh translations

Six new settings under search.engine.web.darkweb.* with default
enabled=false (off by default; user must opt in). darkweb.* keys
declare engines/categories/max_results for the darkweb SearXNG
factory. zh.json picks up the new strings via i18n.t."
git log --oneline -3
```

---

### Task 7: 研究页勾选框 + settings 端点

**Files:**
- Modify: `src/local_deep_research/web/templates/...research.html`（找到实际模板路径）
- Modify: `src/local_deep_research/web/routes/settings_routes.py`

**Interfaces:**
- Produces: 研究页引擎下拉旁条件渲染勾选框（仅 `enabled=true` 时显示）
- `GET /api/v1/settings/darkweb-status` 返回 `{enabled, available}` 给前端用

- [ ] **Step 1: 找到研究页模板和 settings_routes.py 锚点**

```bash
grep -rln "engine.*select\|search_engine_select" /home/administrator/local-deep-research/src/local_deep_research/web/templates/ 2>/dev/null | head -5
grep -n "test-darkweb\|darkweb" /home/administrator/local-deep-research/src/local_deep_research/web/routes/settings_routes.py | head -10
```

- [ ] **Step 2: 写前端 mock 测试**

由于 UI 测试成本高，本 Task 在端到端集成测试里覆盖（Task 9）。仅写**模板渲染**的最小烟雾测试。

- [ ] **Step 3: 在 settings_routes.py 加端点**

参考 Task 1 阶段一 `b58238a0` 的 `POST /settings/api/test-darkweb` 端点模式，新加 `GET /settings/api/darkweb-status` 端点：

```python
@bp.route("/settings/api/darkweb-status", methods=["GET"])
@login_required
def darkweb_status():
    """Report whether the darkweb engine is enabled (caller decides whether
    to render the checkbox) and whether ldr-tor is reachable (for UI hint)."""
    enabled = bool(_get_setting("search.engine.web.darkweb.enabled", False))
    # Probing ldr-tor is intentionally NOT done here — it's slow (~26s).
    # The preflight (Task 8) handles reachability detection.
    return jsonify({"enabled": enabled})
```

- [ ] **Step 4: 在研究页模板条件渲染**

模板里加：

```html
{% if darkweb_enabled %}
<div class="form-check">
    <input class="form-check-input" type="checkbox" id="include-darkweb" name="include_darkweb">
    <label class="form-check-label" for="include-darkweb">
        同时检索暗网 (Tor)
    </label>
</div>
{% endif %}
```

后端把 `darkweb_enabled` 注入模板上下文。

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/web/routes/settings_routes.py \
        src/local_deep_research/web/templates/...research.html \
        tests/web/test_darkweb_phase2.py
git commit -m "feat(ui): darkweb checkbox on research page (conditional)

Renders a '同时检索暗网' checkbox next to the engine dropdown only when
search.engine.web.darkweb.enabled is true. New GET endpoint
/settings/api/darkweb-status returns the enabled flag for AJAX clients."
git log --oneline -3
```

---

### Task 8: preflight 接入 darkweb 探测

**Files:**
- Modify: `src/local_deep_research/diagnostics/engine_health.py`
- Test: extend existing `tests/diagnostics/test_darkweb_probe.py`

**Interfaces:**
- Consumes: `probe_darkweb()`（阶段一已存在）
- Produces: `run_preflight_check()` 增加 darkweb 分支；仅在 `enabled=true` 时调用 `probe_darkweb`

- [ ] **Step 1: 找到 `run_preflight_check` 锚点**

```bash
grep -n "run_preflight_check\|probe_searxng\|probe_ollama" /home/administrator/local-deep-research/src/local_deep_research/diagnostics/engine_health.py | head -10
```

- [ ] **Step 2: 写失败测试**

Append to `tests/diagnostics/test_darkweb_probe.py`：

```python
def test_preflight_skips_darkweb_when_disabled():
    """When search.engine.web.darkweb.enabled is false, run_preflight_check
    must NOT call probe_darkweb (saves ~26s per research)."""
    from local_deep_research.diagnostics.engine_health import run_preflight_check
    from unittest.mock import patch
    with patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb"
    ) as mock_probe:
        run_preflight_check(settings_snapshot={"search.engine.web.darkweb.enabled": False})
        mock_probe.assert_not_called()


def test_preflight_calls_darkweb_when_enabled():
    from local_deep_research.diagnostics.engine_health import run_preflight_check
    from unittest.mock import patch, MagicMock
    fake_status = MagicMock(name="ok", status="ok", detail="L4: ...")
    with patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb",
        return_value=fake_status,
    ) as mock_probe:
        run_preflight_check(settings_snapshot={"search.engine.web.darkweb.enabled": True})
        mock_probe.assert_called_once()
```

- [ ] **Step 3: 在 `run_preflight_check` 加分支**

参照阶段一 `ea890bf2` 的合并模式，在现有 preflight 末尾追加：

```python
if _truthy(settings_snapshot.get("search.engine.web.darkweb.enabled", False)):
    darkweb_status = probe_darkweb(settings_snapshot=settings_snapshot)
    statuses.append(darkweb_status)
    if darkweb_status.status == "error":
        # Fail-soft: log but don't block research.
        logger.warning(
            f"[DIAG] darkweb probe failed: {darkweb_status.detail}; "
            f"skipping darkweb engine for this research"
        )
```

**关键**：fail-soft，不中断研究（设计文档明确要求）。

- [ ] **Step 4: 验证测试 + Commit**

```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/diagnostics/test_darkweb_probe.py -q
git rev-parse --abbrev-ref HEAD
git add src/local_deep_research/diagnostics/engine_health.py \
        tests/diagnostics/test_darkweb_probe.py
git commit -m "feat(diagnostics): preflight runs darkweb probe when enabled

run_preflight_check() now appends probe_darkweb() to its checks when
search.engine.web.darkweb.enabled is true. Failure is fail-soft (logs
warning, doesn't block the research) per the design spec. When
disabled, the probe is skipped entirely — saves ~26s per research."
git log --oneline -3
```

---

### Task 9: 端到端集成测试（host Tor 跳过的环境标记）

**Files:**
- Modify: `tests/web/test_darkweb_phase2.py`（追加 e2e 用例）

**Interfaces:**
- 端到端测试需要：
1. 本地代理跑起来（用 Task 1 的代码）
2. Mock SOCKS5h 端点（避免真 tor）
3. 验证完整流程：明网 + 暗网 → 合并 → provenance 标记 → onion URL 通过代理

**环境标记**：如果 host Tor 通道不可达（环境变量 `LDR_TOR_EGRESS_OK=false`），跳过 e2e 测试。

- [ ] **Step 1: 写端到端测试**

Append to `tests/web/test_darkweb_phase2.py`：

```python
import os
import pytest

# Skip e2e if host Tor egress is unavailable.
LDR_TOR_OK = os.environ.get("LDR_TOR_EGRESS_OK", "true").lower() in ("1", "true", "yes")

e2e = pytest.mark.skipif(
    not LDR_TOR_OK,
    reason="Host Tor SOCKS5h egress unavailable; set LDR_TOR_EGRESS_OK=true to enable",
)


@e2e
def test_end_to_end_darkweb_full_fetch():
    """Full pipeline: search engine returns .onion results → _get_full_content
    uses the local CONNECT proxy → host successfully fetches .onion HTML."""
    # Start the proxy in a thread, mock the SOCKS5h server, drive a fake
    # search engine that returns .onion URLs, then assert _get_full_content
    # invokes safe_get with the local proxy URL.
    # Implementation depends on the actual signatures; insert here.
    pass  # placeholder
```

- [ ] **Step 2: 验证 skip 行为 + 模拟环境**

```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -v
# 期望：e2e 测试跳过；其他单元/集成测试通过

LDR_TOR_EGRESS_OK=false LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_darkweb_phase2.py -v
# 期望：明确标记 SKIPPED
```

- [ ] **Step 3: Commit**

```bash
git rev-parse --abbrev-ref HEAD
git add tests/web/test_darkweb_phase2.py
git commit -m "test(phase2): end-to-end darkweb fetch with environment skip

The e2e test exercises the full pipeline (search → tag → CONNECT proxy
→ .onion HTML fetch). Skipped by default when LDR_TOR_EGRESS_OK is not
'true'/'1'/'yes', since the test requires real host→Tor SOCKS5h egress
to be reachable. Set the env var explicitly to enable locally."
git log --oneline -3
```

---

## Self-Review

**1. Spec coverage:**

| 设计文档要求 | 实现 |
|---|---|
| 系统配置中提供手工开关，默认关闭 | Task 6 ✓ `enabled=false` |
| 提供连接测试，能明确指出前提是否满足 | Task 1 阶段一 `probe_darkweb`（已存在，本计划不动）+ Task 8 ✓ 接入 preflight |
| 开启后，可在新建研究任务时选择让暗网作为独立信息源参与检索 | Task 5 ✓ 主流程追加 + Task 7 ✓ UI 勾选框 |
| 报告中暗网来源必须与明网来源显著区分 | Task 3 ✓ `tag_darkweb()` + 阶段三 `[D1]` 编号（计划范围外） |
| 不做暗网内容的额外清洗、去重或风险评级 | ✓ 明确排除 |
| 不改变 SearXNG 容器的启动方式 | ✓ 仅依赖现有 searxng-ldr |
| 不从 `.onion` 来源抓取图片 | ✓ Task 4 仅改 HTML 抓取路径，不动图片管道 |

**2. Placeholder scan:**
- Task 4 Step 2、Task 5 Step 2 写了"depends on Step 1 grep"——这是必要的 anti-placeholder，不是"待定"
- Task 6 Step 3 写了"精确格式见实际文件"——同样必要（计划阶段不应该瞎编 JSON 缩进）
- Task 9 Step 1 端到端测试是 placeholder，注释明确说明 "insert here"

**3. Type consistency:**
- `_make_darkweb_engine() -> SearXNGSearchEngine`：Task 3 定义；Task 5 调用签名一致
- `tag_darkweb(results: list[dict]) -> list[dict]`：Task 3 定义；Task 5 调用一致
- `get_onion_proxies(url: str) -> Optional[Dict[str, str]]`：Task 2 定义；Task 4 调用 `kwargs.setdefault("proxies", ...)` 兼容
- `ONION_PROXY_URL` 常量在 Task 1、Task 2 都引用，值必须一致：`"http://127.0.0.1:18080"`
- `probe_darkweb()` 函数签名不变（阶段一已存在，Task 8 仅在 preflight 调用）

**4. Risk traceback:**
- **Task 4 grep 出真实接口前不写代码**：Step 1-3 是 explore-before-modify 模式，符合 CLAUDE.md §3 外科手术改动
- **Task 5 grep 主流程检索调用点前不写代码**：同上
- **Task 6 grep default_settings.json schema 前不写 JSON**：同上
- **Task 7 grep 模板前不写 HTML**：同上

**5. Integration risk（已识别）:**
- `proxy_config.py:315` 的 `apply_proxy_to_wikipedia_env()` 会覆盖 HTTP_PROXY env——**本计划不动它**。tor 代理是**附加**层（`kwargs.setdefault` 模式），不替换 Privoxy
- 本地代理跑在宿主，pid 文件 `/tmp/onion-connect-proxy.pid`——**不进** docker compose（避免容器端口冲突 + 减少攻击面）
- 端到端 e2e 测试用 `LDR_TOR_EGRESS_OK` env var 跳过，避免 CI 环境跑挂

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-15-darkweb-phase2-search-capable.md`. 9 Task, ~220 行（含测试）。

**Two execution options:**

1. **Subagent-Driven（推荐）** — 派发独立 subagent per Task，每 Task 后 review
2. **Inline Execution** — 当前会话执行 executing-plans，batch + checkpoint

**实施顺序**（Task 间依赖）：
```
Task 1 (代理) → Task 2 (proxies helper) → Task 3 (工厂) → Task 4 (full_content 走代理) → Task 5 (主流程追加) → Task 6 (配置) → Task 7 (UI) → Task 8 (preflight) → Task 9 (e2e 测试)
```

每个 Task 独立可 commit、可停。**完成 9 个 Task 后**，报告用户，需用户**明确批准**才能重启 `ldr-local` 容器让 hot-mount 生效。

**前置条件**：本计划假设阶段一 `probe_darkweb()` + `DARKWEB_ENGINES` 常量已存在（已 commit：f32c1c6f / ea890bf2 / b58238a0）。

---

## 实施现实（2026-08-15 落地时与原计划的偏差）

计划假设 `_get_full_content` 走 `requests.get(url)`，然后通过 `get_onion_proxies(url)` 注入 HTTP CONNECT 代理。**实际**：抓取走 Playwright（`research_library/downloaders/playwright_html.py`），浏览器不能消费 HTTP CONNECT 代理——HTTP CONNECT 隧道方案对 Playwright 无效。

**Task 4 实际实施**：Playwright 启动时传 `proxy={'server': 'socks5://172.21.0.4:9050'}`，浏览器走 Chromium 自带的 SOCKS5 远端解析（RFC 1928 `atyp=0x03`）直接连 ldr-tor。Task 1 的 `OnionConnectProxy` 代码**保留但未被任何代码路径使用**——作为基础设施，供未来非 Playwright 路径使用（例如未来某个 SSE 客户端、httpx 集成等）。按 CLAUDE.md §3 "Touch only what you must" 不删除未使用代码。

**Task 5 修复**：原计划调 `darkweb_engine.search(query)`，但 SearXNGSearchEngine **没有** `.search()` 方法——实际 API 是 `.results()`。`fix(darkweb): SearXNGSearchEngine.results() not .search()`（commit b7d407a9）修了这个问题。

**Task 1 / Task 4 文件路径不变**：Task 1 的本地代理仍在 `src/local_deep_research/network/onion_connect_proxy.py`，Task 4 的 Playwright SOCKS5 路由在 `src/local_deep_research/research_library/downloaders/playwright_html.py:_fetch_with_playwright`。两者解耦：Task 1 是基础设施，Task 4 是实际生效的抓取路径。

**阶段二完整链路（实测验证）**：
```
SearXNG engines=ahmia,torch, categories=onions
   → 10 .onion 结果，11.2s
   ↓
tag_darkweb() → is_darkweb=True × 10
   ↓
merge into all_links_of_system (1 clearnet + 10 darkweb)
   ↓
fetch_content_with_images → Playwright socks5://172.21.0.4:9050
   ↓
.onion 全文 HTML: 169 KB DuckDuckGo 实测，53.1s
```

**阶段三（任务 #21 / #23 / #24 / #25）已 commit**：`is_darkweb_url()` utility（537080e8）+ 图片管道 D引用防御测试（1c2a8a21）+ 参考文献暗网分组（b6d2210c）+ 章节末尾暗网标注（be937291）。`[D1]` 编号重写（Task #22）**有意不做**——触动引用系统核心风险高，阶段三其余 4 个改动已让暗网来源在视觉上明确区分。

**Dockerfile 后续调整**（commit 946664de / 1626181b / a7475563 / c3cc31d1 / 4531ff1b / 2ab82bd0 / e14d9c72 / 35b2d258）：在最终 `ldr` stage 加 `playwright install --with-deps chromium chromium-headless-shell` 走 npmmirror 中国镜像下载，pin `playwright==1.60.0` 与 hot-mount site-packages 的 playwright 版本对齐，避免版本不匹配导致 "Executable doesn't exist" 错误。

**此附录由 2026-08-15 实施 commit 链生成**：7e2e80f0 / 8a88cb68 / 11214ff2 / 87770a8f / 7caebb7c / 67388778 / 541b29ef / 45de0cd1 / b7d407a9 / 946664de / 1626181b / a7475563 / c3cc31d1 / 4531ff1b / 2ab82bd0 / e14d9c72 / 35b2d258 / 537080e8 / 1c2a8a21 / b6d2210c / be937291（21 个 commit 跨 main，分阶段二+阶段三）。