"""Proxy-outage hard abort in preflight (source-structure pin).

Observed 2026-08-26 (user preflight report): proxy errored
('TCP 通但无法经代理出站') and every proxied engine died with it —
only wikidata/yandex/firecrawl survived. The research still ran and
burned minutes before failing on suspended engines. User decision:
a dead proxy (the transport ALL engines depend on in this
deployment) must abort the research immediately with a clear
message, like the Tor-circuit abort.
"""

import inspect

import local_deep_research.web.services.research_service as rs


def test_proxy_abort_exists_after_tor_abort():
    src = inspect.getsource(rs.run_research_process)
    tor_pos = src.index("abort reason=tor_circuit_unavailable")
    proxy_pos = src.index("abort reason=proxy_outage_unavailable")
    system_pos = src.index("system = AdvancedSearchSystem(")
    assert tor_pos < proxy_pos < system_pos, (
        f"proxy abort must sit with the tor abort, before system init: "
        f"tor@{tor_pos} proxy@{proxy_pos} system@{system_pos}"
    )


def test_proxy_abort_message():
    src = inspect.getsource(rs.run_research_process)
    block = src[src.index("reason=proxy_outage_unavailable") : src.index("system = AdvancedSearchSystem(")]
    assert "Proxy网络连接错误" in block
    assert "请检测VPN代理配置" in block
    assert "raise ValueError" in block


def test_proxy_abort_predicates_on_kind_and_status():
    """The predicate must match the proxy row by kind='proxy' +
    error/timeout (not by name — probe names can change), and must
    NOT fire on 'ok' or 'skipped'."""
    src = inspect.getsource(rs.run_research_process)
    # widen the block: the predicate sits BEFORE the abort log marker
    block = src[src.index("_proxy_dead = next") : src.index("system = AdvancedSearchSystem(")]
    assert ".kind ==" in block and '"proxy"' in block
    assert '"error", "timeout"' in block
