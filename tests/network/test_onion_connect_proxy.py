"""Unit tests for the local HTTP CONNECT proxy.

These tests exercise the proxy logic with a *mock* SOCKS5 server, so they
do NOT require ldr-tor to be reachable. The end-to-end test against a
real .onion URL lives in tests/web/test_darkweb_phase2.py and is skipped
when the host-side Tor egress is unavailable.
"""
import socket
import threading
import time

import pytest

from local_deep_research.network.onion_connect_proxy import OnionConnectProxy


def _run_until(predicate, timeout=2.0, interval=0.01):
    """Wait up to ``timeout`` seconds for ``predicate()`` to return truthy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def proxy_with_socks_stub():
    """Start an OnionConnectProxy bound to an ephemeral port, with a
    single-shot TCP stub pretending to be the SOCKS5h server.

    Yields ``(proxy_real_port, socks_stub_server_socket)``. The stub
    server has already been bound+listening; tests should accept on it
    to simulate Tor's reply.
    """
    stub = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stub.bind(("127.0.0.1", 0))
    stub.listen(1)
    socks_port = stub.getsockname()[1]

    p = OnionConnectProxy(
        port=0,
        tor_host="127.0.0.1",
        tor_port=socks_port,
        strict=False,  # tests override per-case after fixture yields
    )
    t = threading.Thread(target=p.serve_forever, daemon=True)
    t.start()
    # Wait until the proxy is actually accepting connections.
    assert _run_until(lambda: p.port > 0), "proxy did not bind"
    real_port = p.port

    try:
        yield p, real_port, stub
    finally:
        p._close()
        stub.close()


def test_default_is_strict():
    """strict must default to True to prevent accidental non-onion routing."""
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=9050)
    assert p.strict is True


def test_strict_rejects_non_onion_host():
    """strict mode (default) returns 403 for non-.onion targets."""
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=1, strict=True)
    t = threading.Thread(target=p.serve_forever, daemon=True)
    t.start()
    _run_until(lambda: p.port > 0)
    real_port = p.port
    try:
        client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
        client.sendall(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        resp = client.recv(4096)
        assert b"403" in resp
        client.close()
    finally:
        p._close()


def test_non_strict_relays_to_upstream(proxy_with_socks_stub):
    """non-strict mode reaches the (mock) SOCKS5 upstream — handshake
    completes, the 200 OK reaches the client. This proves the proxy
    actually tunnels, not just that it accepts the connection."""
    p, real_port, stub = proxy_with_socks_stub
    handshake_done = threading.Event()

    def handle_upstream(s):
        try:
            data = s.recv(4096)
            assert data[:3] == b"\x05\x01\x00", f"unexpected greeting: {data!r}"
            s.sendall(b"\x05\x00")
            data = s.recv(4096)
            assert data[0:3] == b"\x05\x01\x00", f"unexpected connect: {data!r}"
            s.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            handshake_done.set()
            # Keep socket open briefly so the proxy sees the SOCKS5 success
            # and starts its relay loop. Then close.
            time.sleep(0.05)
        finally:
            s.close()

    acceptor = threading.Thread(
        target=lambda: handle_upstream(stub.accept()[0]), daemon=True
    )
    acceptor.start()

    client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
    client.sendall(
        b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
    )
    resp = client.recv(4096)
    assert b"200 OK" in resp, f"got: {resp!r}"
    assert handshake_done.wait(timeout=2), "SOCKS5 handshake did not complete"
    client.close()


def test_400_on_non_connect_request(proxy_with_socks_stub):
    """Malformed request lines get 400, not 200 or 403."""
    p, real_port, _stub = proxy_with_socks_stub
    client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
    client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    resp = client.recv(4096)
    assert b"400" in resp
    client.close()