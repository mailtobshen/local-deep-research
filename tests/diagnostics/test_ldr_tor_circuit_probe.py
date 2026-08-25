"""probe_ldr_tor_proxy 的第 3 级探测：真实 onion 电路验证。

背景（research 31c9bdb2，2026-08-25 12:17）：L0 探测只做 TCP 连接
+ SOCKS5 greeting，而 ldr-tor 容器在"端口活着但 0 circuits"的状态下
两个检查都通过（预检显示 ✓ ldr-tor，1ms）。3 分钟后研究才经
no_results 哨兵中止 —— 用户白等 3 分钟才看到"未搜索到相关结果"。

本测试钉住第 3 级：经 SOCKS5 对一个已知 .onion 地址发起 CONNECT
（远程解析，ATYP=0x03 域名），电路健康时 SOCKS5 回 REP=0x00 成功；
0 circuits 时回 REP=0x01（一般性失败）或连接超时 —— 探测必须把这种
状态报为 error "Tor 电路不可用"，而不是 ok。
"""

import socket
from unittest.mock import patch

from local_deep_research.diagnostics.engine_health import (
    EngineStatus,
    probe_ldr_tor_proxy,
)

_SNAPSHOT_DARKWEB = {
    "search.tool": "darkweb",
}


def _greeting_ok_then(rep_reply):
    """Build a fake socket whose SOCKS5 greeting succeeds (0x05 0x00)
    and whose CONNECT reply is *rep_reply* (5 bytes: VER REP RSV ATYP...)."""

    class FakeSock:
        def __init__(self):
            self.sent = b""
            self._replies = [bytes([0x05, 0x00]), rep_reply]
            self._stage = 0

        def sendall(self, data):
            self.sent += data

        def settimeout(self, timeout):
            pass

        def recv(self, n):
            reply = self._replies[self._stage]
            self._stage += 1
            return reply

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return FakeSock()


def _run_probe(fake_sock):
    with (
        patch(
            "local_deep_research.diagnostics.engine_health."
            "socket.create_connection",
            return_value=fake_sock,
        ),
        patch(
            "local_deep_research.diagnostics.engine_health."
            "get_setting_from_snapshot",
            side_effect=lambda key, default=None, **kw: (
                _SNAPSHOT_DARKWEB.get(key, default)
            ),
        ),
        patch(
            "local_deep_research.diagnostics.engine_health."
            "get_bool_setting_from_snapshot",
            side_effect=lambda key, default=False, **kw: default,
        ),
    ):
        return probe_ldr_tor_proxy(_SNAPSHOT_DARKWEB)


class TestCircuitLevelProbe:
    def test_dead_circuit_reports_error(self):
        """SOCKS5 daemon answers the greeting (port alive) but the
        onion CONNECT fails (REP=0x01, what a 0-circuits tor returns)
        — the probe must report error with 电路不可用, NOT ok.
        This is exactly the 31c9bdb2 failure mode."""
        status = _run_probe(_greeting_ok_then(bytes([0x05, 0x01, 0x00, 0x01, 0x00])))
        assert isinstance(status, EngineStatus)
        assert status.status == "error", (
            f"0-circuit tor must be error, got {status.status}: {status.detail}"
        )
        assert "Tor 电路不可用" in status.detail

    def test_live_circuit_reports_ok(self):
        """REP=0x00 (CONNECT succeeded through a real circuit) → ok."""
        status = _run_probe(_greeting_ok_then(bytes([0x05, 0x00, 0x00, 0x01, 0x00])))
        assert status.status == "ok", (
            f"healthy circuit must be ok, got {status.status}: {status.detail}"
        )

    def test_connect_request_uses_remote_resolution(self):
        """The CONNECT request must carry ATYP=0x03 (domain, remote
        resolve) — ATYP=0x01 would try to resolve the .onion locally
        and always fail before touching the circuit."""
        sock = _greeting_ok_then(bytes([0x05, 0x00, 0x00, 0x01, 0x00]))
        _run_probe(sock)
        # second sendall is the CONNECT request
        assert bytes([0x05, 0x01, 0x00, 0x03]) in sock.sent, (
            f"CONNECT request lacks ATYP=0x03 remote-resolution form: {sock.sent!r}"
        )
