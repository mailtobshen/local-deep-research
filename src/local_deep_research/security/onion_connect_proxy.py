"""Tiny in-process HTTP CONNECT → SOCKS5 forwarder.

Background
----------
``security/proxy_config.py`` already declares
``ONION_PROXY_URL = "http://127.0.0.1:18080"`` and returns it from
``get_onion_proxies(url)`` for any URL whose hostname ends in
``.onion``. The intent is that the LDR downloaders (HTML/Playwright)
route ``.onion`` HTTP(S) through this local endpoint, which then
forwards the connection to the ldr-tor SOCKS5 proxy at
``ldr-tor:9050``.

Before this module existed the URL was a *promise* — the local
endpoint was never actually listening, so every ``.onion`` request
failed with ``Failed to resolve hostname .onion`` at the SSRF
validator (the kernel resolver cannot resolve onion names without
going through Tor) and the deferred-image-fill pass on darkweb task
83a26e94 spent 44.7 minutes hitting that same wall 111 times.

Implementation
--------------
Pure stdlib: a single daemon thread accepts HTTP ``CONNECT`` requests
on ``127.0.0.1:18080`` (configurable via ``LDR_ONION_PROXY_PORT``),
opens a SOCKS5 connection to ``ldr-tor:9050`` (configurable via
``LDR_TOR_SOCKS_HOST`` / ``LDR_TOR_SOCKS_PORT``), and bidirectionally
pipes bytes between the two sockets. Authentication and Tor DNS
resolution are delegated to the tor daemon — Chromium / Playwright
already speak SOCKS5 remote-resolve (RFC 1928), so no extra client
plumbing is required.

This module is *intentionally* small. It is not a hardened proxy:
``127.0.0.1`` only, no TLS, no auth. It exists to unblock the
existing ``get_onion_proxies`` contract.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import threading
from typing import Optional, Tuple

from loguru import logger


_LISTEN_HOST = "127.0.0.1"
_LISTEN_PORT = int(os.environ.get("LDR_ONION_PROXY_PORT", "18080"))
_TOR_SOCKS_HOST = os.environ.get("LDR_TOR_SOCKS_HOST", "ldr-tor")
_TOR_SOCKS_PORT = int(os.environ.get("LDR_TOR_SOCKS_PORT", "9050"))

# SOCKS5 handshake: VER=5, NMETHODS=1, METHODS=[0 (no auth)].
_SOCKS5_NOAUTH_REQUEST = bytes([0x05, 0x01, 0x00])
_SOCKS5_CONNECT_REQUEST_PREFIX = bytes([0x05, 0x01, 0x00])  # VER, CMD=CONNECT, RSV
_SOCKS5_REPLY_GRANTED = bytes([0x05, 0x00])


class OnionConnectProxyError(RuntimeError):
    """Raised when the local CONNECT proxy cannot start or handshake fails."""


def _encode_socks5_connect(host: str, port: int) -> bytes:
    """Build a SOCKS5 CONNECT request for ``host:port``.

    Uses the DOMAINNAME address type (0x03) — this is what makes
    remote-resolve work; the tor daemon resolves the .onion name on
    our behalf. A literal IPv4/IPv6 type would push the resolution
    back to the client, which is exactly what we want to avoid.
    """
    try:
        encoded_host = host.encode("idna").decode("ascii").encode("ascii")
    except UnicodeError:
        encoded_host = host.encode("ascii", errors="replace")
    if len(encoded_host) > 255:
        raise OnionConnectProxyError(
            f"hostname too long for SOCKS5 DOMAINNAME: {host!r}"
        )
    return (
        _SOCKS5_CONNECT_REQUEST_PREFIX
        + bytes([0x03, len(encoded_host)])
        + encoded_host
        + struct.pack("!H", port)
    )


def _socks5_connect(host: str, port: int) -> socket.socket:
    """Open a SOCKS5 connection through tor and return the ready socket."""
    tor = socket.create_connection((_TOR_SOCKS_HOST, _TOR_SOCKS_PORT), timeout=10)
    try:
        tor.sendall(_SOCKS5_NOAUTH_REQUEST)
        greeting = tor.recv(2)
        if greeting != bytes([0x05, 0x00]):
            raise OnionConnectProxyError(
                f"SOCKS5 greeting rejected by tor: {greeting.hex()}"
            )
        tor.sendall(_encode_socks5_connect(host, port))
        # Reply: VER, REP, RSV, ATYP, then address+port (BND.ADDR/BND.PORT).
        # We only need to confirm REP=0x00 (success); the rest is the
        # tor-side bind address which the caller discards.
        reply_head = tor.recv(4)
        if len(reply_head) < 4:
            raise OnionConnectProxyError(
                f"SOCKS5 CONNECT reply truncated: {reply_head.hex()}"
            )
        if reply_head[:2] != _SOCKS5_REPLY_GRANTED:
            rep = reply_head[1]
            raise OnionConnectProxyError(
                f"SOCKS5 CONNECT refused by tor (REP={rep:#x})"
            )
        atyp = reply_head[3]
        if atyp == 0x01:  # IPv4
            tor.recv(4 + 2)
        elif atyp == 0x03:  # DOMAINNAME
            ln = tor.recv(1)[0]
            tor.recv(ln + 2)
        elif atyp == 0x04:  # IPv6
            tor.recv(16 + 2)
        else:
            raise OnionConnectProxyError(f"SOCKS5 reply ATYP unknown: {atyp}")
    except Exception:
        tor.close()
        raise
    return tor


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """Forward bytes between two sockets until either side closes."""
    try:
        while True:
            # Use select so a peer close wakes us immediately rather
            # than waiting for the recv timeout.
            rlist, _, _ = select.select([src], [], [], 30)
            if not rlist:
                # idle timeout; both sides probably stalled, give up
                return
            chunk = src.recv(65536)
            if not chunk:
                return
            dst.sendall(chunk)
    except (OSError, ConnectionError):
        return
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _handle_client(client: socket.socket) -> None:
    """Process one HTTP CONNECT request then tunnel until EOF."""
    try:
        client.settimeout(10)
        # Read the request line: ``CONNECT host:port HTTP/1.1``.
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            chunk = client.recv(1024)
            if not chunk:
                return
            buf += chunk
        if not buf.startswith(b"CONNECT "):
            client.sendall(
                b"HTTP/1.1 400 Bad Request\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            return
        request_line = buf.split(b"\r\n", 1)[0]
        target = request_line[len(b"CONNECT ") :].rsplit(b" ", 1)[0]
        host_b, _, port_b = target.partition(b":")
        host = host_b.decode("ascii", errors="replace")
        try:
            port = int(port_b)
        except ValueError:
            port = 443
        if not host or not (1 <= port <= 65535):
            client.sendall(
                b"HTTP/1.1 400 Bad Request\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            return
        # Only forward .onion targets — this proxy exists for one job.
        if not (host == "onion" or host.endswith(".onion")):
            client.sendall(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            logger.warning(
                f"[onion-connect-proxy] refused non-.onion target {host}:{port}"
            )
            return
        tor = _socks5_connect(host, port)
    except OnionConnectProxyError as exc:
        logger.warning(f"[onion-connect-proxy] CONNECT failed: {exc}")
        try:
            client.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
        except OSError:
            pass
        client.close()
        return
    except Exception:
        logger.exception("[onion-connect-proxy] client handshake crashed")
        try:
            client.close()
        except OSError:
            pass
        return

    # Tunnel established — tell the client to proceed.
    client.sendall(
        b"HTTP/1.1 200 Connection Established\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: keep-alive\r\n\r\n"
    )
    client.settimeout(None)
    _pipe(client, tor)


def start_onion_connect_proxy(
    host: str = _LISTEN_HOST,
    port: int = _LISTEN_PORT,
    tor_host: str = _TOR_SOCKS_HOST,
    tor_port: int = _TOR_SOCKS_PORT,
) -> Optional[Tuple[str, int]]:
    """Start the local CONNECT proxy in a daemon thread.

    Returns ``(host, port)`` of the bound listener on success, or
    ``None`` if it could not start (e.g. tor is unreachable, port
    already in use, or another instance is already listening).

    Safe to call more than once: a second call returns the existing
    bound address without spawning a second listener. This matches
    Flask ``create_app()`` being called multiple times in tests.
    """
    global _LISTEN_PORT, _TOR_SOCKS_HOST, _TOR_SOCKS_PORT
    _LISTEN_PORT = port
    _TOR_SOCKS_HOST = tor_host
    _TOR_SOCKS_PORT = tor_port

    # Re-entrancy guard: at most one listener per process.
    state = start_onion_connect_proxy.__dict__
    if state.get("_listener_started"):
        return state.get("_bound_address")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
    except OSError as exc:
        logger.warning(
            f"[onion-connect-proxy] could not bind {host}:{port}: {exc}"
        )
        server.close()
        return None
    server.listen(64)
    server.settimeout(None)

    def _accept_loop() -> None:
        logger.info(
            f"[onion-connect-proxy] listening on {host}:{port} "
            f"(forwarding to tor socks5 {tor_host}:{tor_port})"
        )
        try:
            while True:
                client, _ = server.accept()
                t = threading.Thread(
                    target=_handle_client,
                    args=(client,),
                    name="onion-connect-proxy-client",
                    daemon=True,
                )
                t.start()
        except Exception:
            logger.exception("[onion-connect-proxy] accept loop crashed")

    threading.Thread(
        target=_accept_loop,
        name="onion-connect-proxy",
        daemon=True,
    ).start()

    state["_listener_started"] = True
    state["_bound_address"] = (host, port)
    return host, port