"""Pre-flight engine health checks.

Probes the SearXNG instance and (if enabled) the Firecrawl service to report
which search backends are currently returning results. Used both as a standalone
CLI diagnostic (`scripts/check_engines.py`) and as an automatic pre-flight step
inside every research task (`research_service.py`).

Design:
- Pure detection logic; no LDR runtime state mutation.
- All HTTP via ``safe_get`` (inherits SSRF + proxy-bypass policy; allows the
  RFC1918 SearXNG/Firecrawl hosts via ``allow_private_ips=True``).
- Each SearXNG backend is exercised with one real search query so the result
  reflects live upstream behaviour (rate-limiting, CAPTCHA, proxy reachability),
  not just historical counters.
- Probes run in parallel with a short per-probe timeout; failures never raise
  out of ``run_preflight_check``.
"""
from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests
from loguru import logger

from ..config.thread_settings import (
    get_bool_setting_from_snapshot,
    get_setting_from_snapshot,
)

# Curated allowlist of SearXNG backends worth probing (mirrors the operators'
# searxng/settings.yml). SearXNG's ``general`` category has ~50 backends, many
# non-search (translation, currency, dict); probing only real search engines
# keeps the pre-flight fast and the output meaningful. Names must match
# SearXNG's engine names (e.g. ``google`` ships as ``google cse`` when the CSE
# variant is active).
_FALLBACK_ENGINES = [
    "bing",
    "google",
    "google cse",
    "google news",
    "mwmbl",
    "wikipedia",
    "wikidata",
    "yahoo",
    "yandex",
]
# Per-engine category override. Most engines live in SearXNG's "general"
# category, but some (e.g. "google news") are scoped to other categories.
# Anything not listed here defaults to "general" in the probe.
_ENGINE_CATEGORIES: dict[str, str] = {
    "google news": "news",
}
DEFAULT_SEARXNG_URL = "http://searxng-ldr:8080"
DEFAULT_FIRECRAWL_URL = "http://localhost:3002"
_PROBE_QUERY = "test"
_PROBE_TIMEOUT = 60  # seconds per probe — SearXNG runs the named engine PLUS all
              # upstream engines that the engine depends on. When the
              # request goes through the host Privoxy (HTTP_PROXY env
              # on ldr-local), the round-trip can easily exceed 30s
              # for a slow upstream like ahmia.fi, so we budget a full
              # minute per probe and let the run_preflight_check
              # timeout handle aggregate failures separately.
# other enabled engines behind the scenes to populate `unresponsive_engines`,
# so a probe of just one engine still waits for every backend's timeout.
# 30s covers the worst-case aggregation when several slow backends run in
# parallel through the proxy.
_MAX_WORKERS = 8
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html",
}


@dataclass
class EngineStatus:
    """Result of probing one engine or service."""

    name: str
    # "ok" | "error" | "timeout" | "skipped"
    status: str
    detail: str = ""
    latency_ms: int = 0
    # Logical group for display: "searxng" backend or "firecrawl" service.
    kind: str = "searxng"


def _get_searxng_url(settings_snapshot: Optional[dict]) -> str:
    url = get_setting_from_snapshot(
        "search.engine.web.searxng.default_params.instance_url",
        default=DEFAULT_SEARXNG_URL,
        settings_snapshot=settings_snapshot,
    )
    if not isinstance(url, str) or not url:
        url = DEFAULT_SEARXNG_URL
    return url.rstrip("/")


def get_searxng_engines(
    instance_url: str, timeout: int = _PROBE_TIMEOUT
) -> list[str]:
    """Return the engines to probe.

    Strategy: SearXNG's ``general`` category includes ~50 backends, many of
    which (translation, currency, dict) are not real search engines and only
    add noise + latency. So we prefer the curated ``_FALLBACK_ENGINES``
    allowlist (mirrors the operators' ``searxng/settings.yml``), intersected
    with whatever ``/config`` reports as ``enabled`` (so disabled backends
    aren't probed). If the intersection is empty (config unreachable or
    allowlist all disabled), fall back to the full ``general`` enabled list.

    Uses ``requests`` directly (not ``safe_get``): the SearXNG instance is a
    trusted local service, and ``safe_get``'s SSRF validator does a DNS
    lookup that intermittently fails for the container hostname
    (``searxng-ldr``) under Docker's embedded DNS, which would make the
    pre-flight spuriously fail.
    """
    config_enabled: set[str] = set()
    general_enabled: list[str] = []
    try:
        resp = requests.get(
            f"{instance_url}/config",
            timeout=timeout,
            headers=_BROWSER_HEADERS,
        )
        if resp.status_code == 200:
            engines = resp.json().get("engines", [])
            if isinstance(engines, list):
                for e in engines:
                    if not (isinstance(e, dict) and e.get("enabled")):
                        continue
                    name = e.get("name")
                    if not name:
                        continue
                    config_enabled.add(name)
                    if "general" in (e.get("categories") or []):
                        general_enabled.append(name)
    except Exception as e:  # noqa: BLE001 — probe must not raise
        logger.debug(f"SearXNG /config probe failed: {e}")

    # Prefer the curated allowlist, limited to what's actually enabled.
    curated = [n for n in _FALLBACK_ENGINES if n in config_enabled]
    if curated:
        return curated
    if general_enabled:
        return general_enabled
    return list(_FALLBACK_ENGINES)


def get_searxng_all_engines(
    instance_url: str, timeout: int = _PROBE_TIMEOUT
) -> list[str]:
    """Unfiltered /config enabled engines — for membership checks, not probing.

    `get_searxng_engines` intersects with a 9-item allowlist tailored
    for fast preflight probing. Some engines (notably darkweb: ahmia,
    torch) are registered in SearXNG but excluded from the allowlist
    because probing each is too slow or noisy. This helper returns
    the full set so membership tests (e.g. probe_darkweb's L2) can
    see engines that are correctly configured but not probed.

    Uses ``requests`` directly (not ``safe_get``) for the same
    DNS-robustness reason as :func:`get_searxng_engines`.
    """
    try:
        resp = requests.get(
            f"{instance_url}/config",
            timeout=timeout,
            headers=_BROWSER_HEADERS,
        )
        if resp.status_code != 200:
            return []
        engines = resp.json().get("engines", [])
        if not isinstance(engines, list):
            return []
        names: list[str] = []
        for e in engines:
            if not (isinstance(e, dict) and e.get("enabled")):
                continue
            name = e.get("name")
            if name:
                names.append(name)
        return names
    except Exception as e:  # noqa: BLE001 — probe must not raise
        logger.debug(f"SearXNG /config (all-engines) probe failed: {e}")
        return []


def probe_searxng_engine(
    instance_url: str, engine_name: str, timeout: int = _PROBE_TIMEOUT
) -> EngineStatus:
    """Run one real search query against a single SearXNG backend engine.

    Uses ``requests`` directly for the same DNS-robustness reason as
    :func:`get_searxng_engines`.
    """
    start = time.monotonic()
    params = {
        "q": _PROBE_QUERY,
        "engines": engine_name,
        "format": "json",
        "categories": _ENGINE_CATEGORIES.get(engine_name, "general"),
        "pageno": 1,
    }
    try:
        resp = requests.get(
            f"{instance_url}/search",
            params=params,
            timeout=timeout,
            headers=_BROWSER_HEADERS,
        )
    except requests.Timeout:
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(engine_name, "timeout", "请求超时", latency)
    except Exception as e:  # noqa: BLE001
        latency = int((time.monotonic() - start) * 1000)
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg or "name resolution" in msg:
            return EngineStatus(engine_name, "timeout", "请求超时/DNS失败", latency)
        return EngineStatus(engine_name, "error", str(e)[:80], latency)

    latency = int((time.monotonic() - start) * 1000)
    if resp.status_code != 200:
        return EngineStatus(
            engine_name, "error", f"HTTP {resp.status_code}", latency
        )

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return EngineStatus(
            engine_name, "error", "响应非JSON(可能被代理拦截)", latency
        )

    # SearXNG reports per-engine problems inside the JSON payload.
    unresponsive = data.get("unresponsive_engines") or []
    if unresponsive:
        # unresponsive_engines is a list of [engine, reason]
        for entry in unresponsive:
            if isinstance(entry, (list, tuple)) and entry and entry[0] == engine_name:
                reason = entry[1] if len(entry) > 1 else "unknown"
                return EngineStatus(
                    engine_name, "error", _humanize_reason(reason), latency
                )

    results = data.get("results") or []
    if results:
        return EngineStatus(
            engine_name, "ok", f"返回 {len(results)} 条结果", latency
        )
    return EngineStatus(
        engine_name, "error", "无结果(可能被限流或代理问题)", latency
    )


def _humanize_reason(reason: str) -> str:
    r = str(reason).lower()
    if "captcha" in r:
        return "CAPTCHA"
    if "too many" in r or "429" in r or "rate" in r:
        return "429 限流"
    if "timeout" in r:
        return "上游超时"
    return str(reason)[:60]


def probe_firecrawl(
    settings_snapshot: Optional[dict], timeout: int = _PROBE_TIMEOUT
) -> EngineStatus:
    """Probe the Firecrawl service if the master switch is on; else skip.

    Hits ``/v1/scrape`` directly with ``requests`` (rather than
    ``FirecrawlClient.scrape``, which swallows all exceptions into ``None``
    and makes connection-refused indistinguishable from a genuine empty
    response). This surfaces the real failure reason.
    """
    enabled = get_bool_setting_from_snapshot(
        "search.engine.web.firecrawl.enable",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    if not enabled:
        return EngineStatus("firecrawl", "skipped", "未启用", 0, kind="firecrawl")

    api_url = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_url",
        default=DEFAULT_FIRECRAWL_URL,
        settings_snapshot=settings_snapshot,
    )
    api_url = (
        api_url if isinstance(api_url, str) and api_url else DEFAULT_FIRECRAWL_URL
    ).rstrip("/")
    api_key = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_key",
        default="",
        settings_snapshot=settings_snapshot,
    )
    api_key = api_key if isinstance(api_key, str) and api_key else None

    start = time.monotonic()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(
            f"{api_url}/v1/scrape",
            json={"url": "https://example.com", "formats": ["markdown"]},
            headers=headers,
            timeout=timeout,
        )
    except requests.Timeout:
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "firecrawl", "timeout", f"请求超时 ({api_url})", latency, kind="firecrawl"
        )
    except requests.ConnectionError as e:
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "firecrawl",
            "error",
            f"无法连接 ({api_url}) — 检查地址/网络",
            latency,
            kind="firecrawl",
        )
    except Exception as e:  # noqa: BLE001
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "firecrawl", "error", str(e)[:80], latency, kind="firecrawl"
        )

    latency = int((time.monotonic() - start) * 1000)
    if resp.status_code == 429:
        return EngineStatus("firecrawl", "error", "429 限流", latency, kind="firecrawl")
    if resp.status_code != 200:
        return EngineStatus(
            "firecrawl",
            "error",
            f"HTTP {resp.status_code}",
            latency,
            kind="firecrawl",
        )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return EngineStatus(
            "firecrawl", "error", "响应非JSON", latency, kind="firecrawl"
        )
    # Firecrawl wraps scrape output in {"data": {"markdown": "..."}}
    payload = data.get("data") if isinstance(data, dict) else None
    markdown = ""
    if isinstance(payload, dict):
        markdown = payload.get("markdown") or ""
    if markdown:
        return EngineStatus(
            "firecrawl",
            "ok",
            f"抓取成功 ({len(markdown)} 字符)",
            latency,
            kind="firecrawl",
        )
    return EngineStatus(
        "firecrawl",
        "error",
        "返回空内容(服务未就绪或目标抓取失败)",
        latency,
        kind="firecrawl",
    )


def probe_proxy(
    settings_snapshot: Optional[dict], timeout: int = _PROBE_TIMEOUT
) -> EngineStatus:
    """Probe the outbound proxy if it's enabled; else skip.

    ``app.network.proxy_url`` is the single source of truth for the proxy
    (SearXNG outgoing proxy + all LDR downloaders read it). A dead proxy is
    the single most common cause of an all-engines-down pre-flight, so this
    probe is run every time.

    Two-stage check (per operator request):
      1. TCP connect to the proxy host:port — distinguishes "proxy process
         down / wrong port" (connection refused) from a working listener.
      2. A real HTTPS request THROUGH the proxy to a stable external URL —
         a port can be open (e.g. a SOCKS-only or unrelated listener) yet
         fail to proxy HTTP(S), so the TCP check alone is not sufficient.
    """
    enabled = get_bool_setting_from_snapshot(
        "app.network.proxy_enabled",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    if not enabled:
        return EngineStatus("proxy", "skipped", "未启用", 0, kind="proxy")

    proxy_url = get_setting_from_snapshot(
        "app.network.proxy_url",
        default="",
        settings_snapshot=settings_snapshot,
    )
    proxy_url = proxy_url.strip() if isinstance(proxy_url, str) else ""
    if not proxy_url:
        return EngineStatus(
            "proxy", "error", "已启用但未配置 proxy_url", 0, kind="proxy"
        )

    parsed = urlparse(proxy_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return EngineStatus(
            "proxy", "error", f"proxy_url 无法解析主机: {proxy_url}", 0, kind="proxy"
        )

    # Stage 1: TCP connectivity to the proxy listener.
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "proxy",
            "error",
            f"TCP 连接失败 {host}:{port} — 代理未启动/端口错误 ({e.__class__.__name__})",
            latency,
            kind="proxy",
        )

    # Stage 2: real HTTPS request THROUGH the proxy.
    try:
        resp = requests.get(
            "https://www.google.com/generate_204",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            headers=_BROWSER_HEADERS,
        )
    except requests.Timeout:
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "proxy",
            "error",
            f"TCP 通但代理请求超时 ({proxy_url})",
            latency,
            kind="proxy",
        )
    except Exception as e:  # noqa: BLE001
        latency = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "proxy",
            "error",
            f"TCP 通但无法经代理出站: {str(e)[:60]}",
            latency,
            kind="proxy",
        )

    latency = int((time.monotonic() - start) * 1000)
    if resp.status_code in (200, 204):
        return EngineStatus(
            "proxy",
            "ok",
            f"代理出站正常 ({proxy_url})",
            latency,
            kind="proxy",
        )
    return EngineStatus(
        "proxy",
        "error",
        f"代理返回 HTTP {resp.status_code} ({proxy_url})",
        latency,
        kind="proxy",
    )


# 暗网引擎。名称必须与 searxng/settings.yml 中的 `name:` 完全一致。
# 默认是 (ahmia, torch)。用户可在 settings 注册更多（通过合并
# searxng/engines-darkweb.yml.template）并由 settings registry 反映。
DARKWEB_ENGINES: tuple[str, ...] = ("ahmia", "torch")

# Tor 首次建链常需数十秒,远慢于明网引擎。这不是故障。
_DARKWEB_TIMEOUT = 60


def _darkweb_engine_list(settings_snapshot: Optional[dict]) -> tuple[str, ...]:
    """Read the darkweb engine list from settings, falling back to the
    module-level default. Mirrors the resolver in
    web_search_engines/darkweb._resolve_darkweb_engines.
    """
    if settings_snapshot is None:
        return DARKWEB_ENGINES
    raw = settings_snapshot.get(
        "search.engine.web.darkweb.default_params.engines"
    )
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not raw:
        return DARKWEB_ENGINES
    parts = tuple(p.strip() for p in str(raw).split(",") if p.strip())
    return parts or DARKWEB_ENGINES


def _probe_darkweb_single(
    instance_url: str, engine_name: str, timeout: int
) -> EngineStatus:
    """Probe one darkweb engine. Returns a status whose ``name`` is
    ``darkweb/<engine_name>`` so the per-engine preflight table shows
    a row per engine (mirroring how the clearnet table lists each
    searxng backend individually)."""
    start = time.monotonic()
    status = probe_searxng_engine(instance_url, engine_name, timeout=timeout)
    elapsed = int((time.monotonic() - start) * 1000)
    if status.status == "ok":
        return EngineStatus(
            f"darkweb/{engine_name}",
            "ok",
            f"暗网引擎 {engine_name} 可用 (SearXNG)",
            latency_ms=elapsed,
            kind="darkweb",
        )
    return EngineStatus(
        f"darkweb/{engine_name}",
        status.status,
        f"暗网引擎 {engine_name} 不可用 — {status.detail or 'unknown'}",
        latency_ms=elapsed,
        kind="darkweb",
    )


def _darkweb_onion_hits(
    instance_url: str, engines: tuple[str, ...], timeout: int
) -> tuple[int, EngineStatus]:
    """Issue one real darkweb search and return (.onion hit count,
    overall status). The ``inner`` status is for the union query
    (all engines combined) — per-engine connectivity is reported
    separately by ``_probe_darkweb_single``.
    """
    if not engines:
        return 0, EngineStatus(
            "darkweb",
            "error",
            "L2: 暗网引擎列表为空",
            kind="darkweb",
        )
    # Use the first configured engine for the actual L3 query to
    # avoid double-counting when many engines are registered.
    primary = engines[0]
    primary_status = probe_searxng_engine(
        instance_url, primary, timeout=timeout
    )
    if primary_status.status != "ok":
        return 0, primary_status
    params = {
        "q": "market",
        "engines": ",".join(engines),
        "format": "json",
        "categories": "onions",
        "pageno": 1,
    }
    resp = requests.get(
        f"{instance_url}/search", params=params, timeout=timeout
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    hits = sum(
        1
        for r in results
        if ".onion" in str(r.get("url") or "").lower()
    )
    return hits, primary_status


def probe_darkweb(
    settings_snapshot: Optional[dict] = None,
    timeout: int = _DARKWEB_TIMEOUT,
) -> list[EngineStatus]:
    """Per-darkweb-engine preflight. Returns a list of EngineStatus
    entries — one per darkweb engine (``darkweb/ahmia``,
    ``darkweb/torch``, etc.) so the preflight table shows fine-grained
    connectivity like the clearnet table does for searxng backends.

    Each entry walks L1..L3 against the individual engine. L4 is
    encoded in latency_ms.

    Errors (L1 SearXNG unreachable, L2 engine block not merged) are
    propagated to every per-engine entry so the user sees a coherent
    picture: every darkweb engine is reported with the same upstream
    cause.
    """
    engines = _darkweb_engine_list(settings_snapshot)
    start = time.monotonic()
    try:
        instance_url = _get_searxng_url(settings_snapshot)
        # L1
        try:
            available = get_searxng_all_engines(
                instance_url, timeout=timeout
            )
        except Exception as e:  # noqa: BLE001
            return [
                EngineStatus(
                    f"darkweb/{engine_name}",
                    "error",
                    f"L1: SearXNG 不可达 ({instance_url}) — {str(e)[:60]}",
                    kind="darkweb",
                )
                for engine_name in engines
            ]
        # L2
        missing = [e for e in engines if e not in available]
        if missing:
            return [
                EngineStatus(
                    f"darkweb/{engine_name}",
                    "error",
                    (
                        f"L2: SearXNG 未启用 {'/'.join(missing)} — "
                        "引擎块尚未合入 searxng/settings.yml"
                    ),
                    kind="darkweb",
                )
                for engine_name in engines
            ]
        # L3 — per engine. Each engine probe can take up to ``timeout``
        # seconds (a slow ahmia can hang for the full 60s); run them
        # in parallel so the total wall time is bounded by the slowest
        # engine, not the sum.
        with ThreadPoolExecutor(max_workers=len(engines) or 1) as pool:
            per_engine_futs = [
                pool.submit(
                    _probe_darkweb_single, instance_url, engine_name, timeout
                )
                for engine_name in engines
            ]
            union_fut = pool.submit(
                _darkweb_onion_hits, instance_url, engines, timeout
            )
            per_engine = [f.result() for f in per_engine_futs]
            hits, _inner = union_fut.result()
        if hits == 0:
            per_engine.append(
                EngineStatus(
                    "darkweb",
                    "error",
                    "L3: 未取回任何 .onion 结果 — Tor 线路不通或引擎超时",
                    kind="darkweb",
                )
            )
        else:
            per_engine.append(
                EngineStatus(
                    "darkweb",
                    "ok",
                    f"L4: 取回 {hits} 条 .onion 结果",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    kind="darkweb",
                )
            )
        return per_engine
    except Exception as e:  # noqa: BLE001
        return [
            EngineStatus(
                f"darkweb/{engine_name}",
                "error",
                f"探测异常: {str(e)[:70]}",
                kind="darkweb",
            )
            for engine_name in engines
        ]


def run_preflight_check(
    settings_snapshot: Optional[dict] = None,
) -> list[EngineStatus]:
    """Probe SearXNG backends + Firecrawl in parallel.

    When the user has selected a specific primary engine via the
    dropdown (``settings.search.tool``), the per-engine SearXNG
    probe list is restricted to that single engine — probing the
    entire 80+ engine catalogue when the user only intends to
    search one of them wastes 30s+ of preflight time and floods the
    status table with irrelevant results. When the primary is the
    default ``searxng`` meta-engine (i.e. the user has not picked
    a specific backend), we fall back to the curated allowlist.

    Returns a list of :class:`EngineStatus`. Never raises — any probe
    error is captured as a status entry.
    """
    instance_url = _get_searxng_url(settings_snapshot)
    primary_engine = (
        get_setting_from_snapshot(
            "search.tool", "searxng", settings_snapshot=settings_snapshot
        )
        if settings_snapshot
        else "searxng"
    )
    if primary_engine and primary_engine not in ("", "searxng", "auto"):
        # User picked a specific backend — probe only that engine.
        engines = [primary_engine]
    else:
        engines = get_searxng_engines(instance_url)

    logger.info(
        f"[PREFLIGHT] primary_engine={primary_engine!r}, "
        f"engine_count={len(engines)}, engines={engines}"
    )

    statuses: list[EngineStatus] = []

    def _probe_engine(name: str) -> EngineStatus:
        result = probe_searxng_engine(instance_url, name)
        logger.info(
            f"[PREFLIGHT] engine={name!r} status={result.status!r} "
            f"latency_ms={result.latency_ms} detail={result.detail[:60]!r}"
        )
        return result

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        # Proxy first — a dead proxy is the usual root cause of an
        # all-engines-down pre-flight, so surface it at the top.
        proxy_future = pool.submit(probe_proxy, settings_snapshot)
        # SearXNG backends
        engine_futures = {pool.submit(_probe_engine, name): name for name in engines}
        # Firecrawl (in same pool)
        fc_future = pool.submit(probe_firecrawl, settings_snapshot)
        # 暗网探测仅在开关开启时执行 —— 它的超时是 60s,远高于其他引擎,
        # 关闭状态下不应让研究启动为此等待。
        darkweb_enabled = get_bool_setting_from_snapshot(
            "search.engine.web.darkweb.enabled",
            default=False,
            settings_snapshot=settings_snapshot,
        )
        darkweb_future = (
            pool.submit(probe_darkweb, settings_snapshot)
            if darkweb_enabled
            else None
        )

        for fut, name in engine_futures.items():
            try:
                statuses.append(fut.result(timeout=_PROBE_TIMEOUT + 2))
            except FutureTimeout:
                statuses.append(EngineStatus(name, "timeout", "探测超时"))
            except Exception as e:  # noqa: BLE001
                statuses.append(EngineStatus(name, "error", str(e)[:80]))

        # Preserve SearXNG engine order, then append firecrawl last.
        order = {n: i for i, n in enumerate(engines)}
        statuses.sort(key=lambda s: order.get(s.name, len(engines)))

        try:
            statuses.append(
                fc_future.result(timeout=_PROBE_TIMEOUT + 2)
            )
        except FutureTimeout:
            statuses.append(
                EngineStatus("firecrawl", "timeout", "探测超时", kind="firecrawl")
            )
        except Exception as e:  # noqa: BLE001
            statuses.append(
                EngineStatus("firecrawl", "error", str(e)[:80], kind="firecrawl")
            )

        if darkweb_future is not None:
            try:
                # probe_darkweb returns one EngineStatus per darkweb
                # engine (e.g. "darkweb/ahmia", "darkweb/torch") plus
                # a union L3 status. extend() (not append) so the
                # per-engine rows land in the preflight table.
                statuses.extend(
                    darkweb_future.result(timeout=_DARKWEB_TIMEOUT + 5)
                )
            except FutureTimeout:
                # When probe_darkweb itself times out we don't know
                # which engines were configured; fall back to a
                # single ambiguous "darkweb" row.
                statuses.append(
                    EngineStatus(
                        "darkweb", "timeout", "探测超时", kind="darkweb"
                    )
                )
            except Exception as e:  # noqa: BLE001
                statuses.append(
                    EngineStatus(
                        "darkweb", "error", str(e)[:80], kind="darkweb"
                    )
                )

        # Proxy status leads the report (prepended, not appended).
        try:
            proxy_status = proxy_future.result(timeout=_PROBE_TIMEOUT + 2)
        except FutureTimeout:
            proxy_status = EngineStatus(
                "proxy", "timeout", "探测超时", kind="proxy"
            )
        except Exception as e:  # noqa: BLE001
            proxy_status = EngineStatus(
                "proxy", "error", str(e)[:80], kind="proxy"
            )
        statuses.insert(0, proxy_status)

    return statuses


_STATUS_GLYPH = {
    "ok": "✓",
    "error": "✗",
    "timeout": "✗",
    "skipped": "⌀",
}


def format_status_table(statuses: list[EngineStatus]) -> str:
    """Render statuses as a monospace-aligned table for CLI / UI display."""
    if not statuses:
        return "引擎健康预检: (无引擎)"
    name_w = max(len(s.name) for s in statuses)
    status_w = max(len(s.status) for s in statuses)
    lines = ["引擎健康预检:"]
    for s in statuses:
        glyph = _STATUS_GLYPH.get(s.status, "?")
        latency = f" ({s.latency_ms}ms)" if s.latency_ms else ""
        lines.append(
            f"  {glyph} {s.name:<{name_w}}  {s.status:<{status_w}}  {s.detail}{latency}"
        )
    ok = sum(1 for s in statuses if s.status == "ok")
    active = sum(1 for s in statuses if s.status != "skipped")
    lines.append(f"可用引擎/服务: {ok}/{active}")
    return "\n".join(lines)
