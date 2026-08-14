"""Unit tests for the local HTTP CONNECT proxy.

These tests exercise the proxy logic with a *mock* SOCKS5 server, so they
do NOT require ldr-tor to be reachable. The end-to-end test against a
real .onion URL lives in tests/web/test_darkweb_phase2.py and is skipped
when the host-side Tor egress is unavailable.
"""
import socket
import threading

from local_deep_research.network.onion_connect_proxy import OnionConnectProxy


def _serve_one(server_sock: socket.socket, handler) -> None:
    """Accept exactly one connection and run handler."""
    sock, _ = server_sock.accept()
    try:
        handler(sock)
    finally:
        sock.close()


def test_default_is_strict():
    """strict must default to True to prevent accidental non-onion routing."""
    p = OnionConnectProxy(port=0, tor_host="127.0.0.1", tor_port=9050)
    assert p.strict is True


def test_strict_rejects_non_onion_host():
    """strict mode (default) returns 403 for non-.onion targets."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    socks_port = server.getsockname()[1]

    p = OnionConnectProxy(
        port=0, tor_host="127.0.0.1", tor_port=socks_port, strict=True
    )
    p._bind()
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
        server.close()


def test_non_strict_relays_to_upstream():
    """non-strict mode proxies any host through to the (mock) SOCKS5 upstream."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    socks_port = server.getsockname()[1]

    captured = bytearray()

    def handler(s):
        try:
            data = s.recv(4096)
            captured.extend(data)
            s.sendall(b"MOCK-SOCKS5-OK")
        finally:
            s.close()

    t = threading.Thread(
        target=_serve_one, args=(server, handler), daemon=True
    )
    t.start()

    p = OnionConnectProxy(
        port=0, tor_host="127.0.0.1", tor_port=socks_port, strict=False
    )
    p._bind()
    real_port = p.port

    try:
        client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
        client.sendall(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        resp = client.recv(4096)
        assert b"200 OK" in resp, f"got: {resp!r}"
        # The mock upstream echoes a sentinel; non-strict relay should pass it through.
        assert b"MOCK-SOCKS5-OK" in resp
        client.close()
    finally:
        p._close()
        server.close()


def test_400_on_non_connect_request():
    """Malformed request lines get 400, not 200 or 403."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    socks_port = server.getsockname()[1]

    p = OnionConnectProxy(
        port=0, tor_host="127.0.0.1", tor_port=socks_port, strict=True
    )
    p._bind()
    real_port = p.port

    try:
        client = socket.create_connection(("127.0.0.1", real_port), timeout=5)
        client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        resp = client.recv(4096)
        assert b"400" in resp
        client.close()
    finally:
        p._close()
        server.close()