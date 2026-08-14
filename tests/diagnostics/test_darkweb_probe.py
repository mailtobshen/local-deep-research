"""probe_darkweb 的四级下钻诊断。

暗网检索有三个彼此独立的前提：SearXNG 在跑、ahmia/torch 已合入其
settings.yml、Tor 线路能真的取回 .onion 结果。任何一个不满足，症状都
表现为"检索无结果"。四级探测把这三者拆开，让失败直接指向症结。
"""
from unittest.mock import patch

from local_deep_research.diagnostics.engine_health import (
    DARKWEB_ENGINES,
    probe_darkweb,
)


def test_darkweb_engines_are_ahmia_and_torch():
    assert DARKWEB_ENGINES == ("ahmia", "torch")


def test_l1_searxng_unreachable():
    """SearXNG 本身没起来 —— 后三级无从谈起。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        side_effect=OSError("connection refused"),
    ):
        st = probe_darkweb()
    assert st.name == "darkweb"
    assert st.kind == "darkweb"
    assert st.status == "error"
    assert st.detail.startswith("L1:")


def test_l2_engine_block_not_merged():
    """SearXNG 活着但引擎列表里没有 ahmia/torch —— 模板未合入。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["google", "wikipedia"],
    ):
        st = probe_darkweb()
    assert st.status == "error"
    assert st.detail.startswith("L2:")
    assert "ahmia" in st.detail


def test_l3_no_onion_results():
    """引擎已配置但查不到 .onion —— Tor 线路不通或引擎超时。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["ahmia", "torch", "google"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(0, EngineStatus("ahmia", "ok")),
    ):
        st = probe_darkweb()
    assert st.status == "error"
    assert st.detail.startswith("L3:")


def test_l4_ok_reports_hits_and_latency():
    """全通 —— 报告命中数与耗时,供人判断是否值得开启。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["ahmia", "torch"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(7, EngineStatus("ahmia", "ok", latency_ms=4200)),
    ):
        st = probe_darkweb()
    assert st.status == "ok"
    assert st.detail.startswith("L4:")
    assert "7" in st.detail


def test_never_raises_on_unexpected_error():
    """preflight 依赖它绝不抛异常,否则会拖垮整个研究启动。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        side_effect=RuntimeError("boom"),
    ):
        st = probe_darkweb()
    assert st.status == "error"


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
    assert not [s for s in statuses if s.name == "darkweb"]


def test_preflight_includes_darkweb_when_enabled():
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
        return_value=EngineStatus(
            "darkweb", "ok", "L4: 取回 3 条 .onion 结果", kind="darkweb"
        ),
    ):
        statuses = run_preflight_check(
            {"search.engine.web.darkweb.enabled": {"value": True}}
        )

    assert [s for s in statuses if s.name == "darkweb"]
