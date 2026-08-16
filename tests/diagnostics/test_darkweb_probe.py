"""probe_darkweb 的 per-engine preflight 诊断。

暗网检索有 N 个彼此独立的前提（每个 engine 一个）：SearXNG 在跑、
N 个暗网引擎都已合入其 settings.yml、Tor 线路能真的取回 .onion 结果。
任何一个不满足，症状都表现为"检索无结果"。本探测把这 N 个前提拆开，
为每个暗网引擎返回独立的 EngineStatus — 报告粒度与明网一致
（明网按 engine_name 一行一引擎）。
"""
from unittest.mock import patch

from local_deep_research.diagnostics.engine_health import (
    DARKWEB_ENGINES,
    probe_darkweb,
)


def test_darkweb_engines_default_is_ahmia_and_torch():
    assert DARKWEB_ENGINES == ("ahmia", "torch")


def test_get_searxng_all_engines_exists_and_signature():
    """新 helper 必须存在且签名匹配 `(instance_url, timeout=_PROBE_TIMEOUT)`。"""
    import inspect

    from local_deep_research.diagnostics import engine_health

    assert hasattr(engine_health, "get_searxng_all_engines")
    sig = inspect.signature(engine_health.get_searxng_all_engines)
    params = list(sig.parameters.values())
    assert params[0].name == "instance_url"
    timeout_param = sig.parameters.get("timeout")
    assert timeout_param is not None
    assert timeout_param.default is not inspect.Parameter.empty


def test_l1_searxng_unreachable_returns_per_engine_errors():
    """SearXNG 不可达时每个暗网引擎都得一条 L1 error，不能合并。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        side_effect=OSError("connection refused"),
    ):
        statuses = probe_darkweb()
    # One per configured engine, all kind=darkweb, all status=error,
    # all detail starts with L1:.
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert len(darkweb_statuses) == 2  # ahmia + torch
    for s in darkweb_statuses:
        assert s.status == "error"
        assert s.detail.startswith("L1:")
        assert s.name.startswith("darkweb/")  # per-engine naming


def test_l2_engine_block_not_merged_returns_per_engine_errors():
    """SearXNG 活着但引擎列表里没 ahmia/torch — 每个引擎一条 L2 error。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        return_value=["google", "wikipedia"],
    ):
        statuses = probe_darkweb()
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert len(darkweb_statuses) == 2
    for s in darkweb_statuses:
        assert s.status == "error"
        assert s.detail.startswith("L2:")
        assert "ahmia" in s.detail or "torch" in s.detail


def test_l3_per_engine_ok_but_no_onion_results():
    """每个 engine 独立联通 OK, 但 .onion 联合查询 0 命中。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        return_value=["ahmia", "torch", "google"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._probe_darkweb_single",
        side_effect=[
            EngineStatus("darkweb/ahmia", "ok", latency_ms=100),
            EngineStatus("darkweb/torch", "ok", latency_ms=100),
        ],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(0, EngineStatus("darkweb/ahmia", "ok")),
    ):
        statuses = probe_darkweb()
    # Per-engine rows: darkweb/ahmia ok, darkweb/torch ok
    ahmia = next(s for s in statuses if s.name == "darkweb/ahmia")
    torch = next(s for s in statuses if s.name == "darkweb/torch")
    assert ahmia.status == "ok"
    assert torch.status == "ok"
    # Plus a union L3 row showing 0 .onion hits.
    union = next(
        s for s in statuses
        if s.name == "darkweb" and s.detail.startswith("L3")
    )
    assert union.status == "error"


def test_l4_ok_reports_hits_and_latency():
    """全通时每个 engine 各自 ok + union L4 row 显示命中数。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        return_value=["ahmia", "torch"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._probe_darkweb_single",
        side_effect=[
            EngineStatus("darkweb/ahmia", "ok", latency_ms=4200),
            EngineStatus("darkweb/torch", "ok", latency_ms=3800),
        ],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(7, EngineStatus("darkweb/ahmia", "ok", latency_ms=4200)),
    ):
        statuses = probe_darkweb()
    # Two per-engine rows + one union L4 row.
    ahmia = next(s for s in statuses if s.name == "darkweb/ahmia")
    torch = next(s for s in statuses if s.name == "darkweb/torch")
    union = next(
        s for s in statuses
        if s.name == "darkweb" and s.detail.startswith("L4")
    )
    assert ahmia.status == "ok"
    assert torch.status == "ok"
    assert union.status == "ok"
    assert "7" in union.detail


def test_never_raises_on_unexpected_error():
    """preflight 依赖它绝不抛异常, 否则会拖垮整个研究启动。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        side_effect=RuntimeError("boom"),
    ):
        statuses = probe_darkweb()
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert len(darkweb_statuses) == 2
    for s in darkweb_statuses:
        assert s.status == "error"


def test_custom_engine_list_from_settings_snapshot():
    """如果用户在 settings 改了 engines 列表, probe 用用户的列表。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health._get_searxng_url",
        return_value="http://stub:8080",
    ), patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_all_engines",
        return_value=["ahmia", "torch", "haystak"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._probe_darkweb_single",
        side_effect=[
            EngineStatus("darkweb/ahmia", "ok", latency_ms=100, kind="darkweb"),
            EngineStatus("darkweb/torch", "ok", latency_ms=100, kind="darkweb"),
            EngineStatus("darkweb/haystak", "ok", latency_ms=100, kind="darkweb"),
        ],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(1, EngineStatus("darkweb/ahmia", "ok", latency_ms=100, kind="darkweb")),
    ):
        statuses = probe_darkweb(
            settings_snapshot={
                "search.engine.web.darkweb.default_params.engines": {
                    "value": "ahmia,torch,haystak"
                }
            }
        )
    names = {s.name for s in statuses if s.kind == "darkweb"}
    assert "darkweb/ahmia" in names
    assert "darkweb/torch" in names
    assert "darkweb/haystak" in names


def test_preflight_skips_darkweb_when_disabled():
    """开关关闭时不应付出 60 秒探测代价。"""
    from local_deep_research.diagnostics.engine_health import (
        run_preflight_check,
    )

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=[],
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_proxy"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_firecrawl"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb"
    ) as pd:
        statuses = run_preflight_check(
            {"search.engine.web.darkweb.enabled": {"value": False}}
        )

    pd.assert_not_called()
    assert not [s for s in statuses if s.kind == "darkweb"]


def test_preflight_includes_per_darkweb_engine_when_enabled():
    """preflight 跑 probe_darkweb 后每个暗网引擎都得到独立 status。"""
    from local_deep_research.diagnostics.engine_health import (
        EngineStatus,
        run_preflight_check,
    )

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=[],
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_proxy"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_firecrawl"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb",
        return_value=[
            EngineStatus("darkweb/ahmia", "ok", "ok", kind="darkweb"),
            EngineStatus("darkweb/torch", "error", "down", kind="darkweb"),
            EngineStatus(
                "darkweb", "ok", "L4: 取回 3 条 .onion 结果", kind="darkweb"
            ),
        ],
    ):
        statuses = run_preflight_check(
            {"search.engine.web.darkweb.enabled": {"value": True}}
        )
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    names = {s.name for s in darkweb_statuses}
    assert "darkweb/ahmia" in names
    assert "darkweb/torch" in names
    assert "darkweb" in names  # union row