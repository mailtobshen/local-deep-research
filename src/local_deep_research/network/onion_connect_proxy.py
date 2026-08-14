"""Local HTTP CONNECT proxy that tunnels to a SOCKS5h Tor endpoint.

Listens on 127.0.0.1:18080 (configurable) and accepts HTTP CONNECT
requests. Each CONNECT request is forwarded to the configured SOCKS5h
proxy with ``rdns=True`` so the upstream Tor resolves ``.onion``
hostnames. Bytes are relayed bidirectionally between the client and the
upstream.

Strict mode (default) rejects CONNECT requests whose target host does
not end in ``.onion`` with HTTP 403. This prevents accidental
mis-routing of clearnet traffic to Tor exit nodes (which would trigger
Cloudflare CAPTCHAs on the next request).

Run directly::

    python -m local_deep_research.network.onion_connect_proxy

Or programmatically::

    proxy = OnionConnectProxy(port=18080)
    proxy.serve_forever()  # blocks
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading

import socks  # PySocks; already a transitive dep via requests[socks]

log = logging.getLogger(__name__)

DEFAULT_PORT = 18080
DEFAULT_TOR_HOST = "172.21.0.4"
DEFAULT_TOR_PORT = 9050
ONION_SUFFIX = ".onion"


class OnionConnectProxy:
    """HTTP CONNECT -> SOCKS5h tunnel for .onion hostnames.

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
        """Bind the listening socket; updates self.port to the real port."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self.port = self._server.getsockname()[1]
        self._server.listen(8)
        log.info(
            "OnionConnectProxy listening on 127.0.0.1:%d -> SOCKS5h %s:%d (strict=%s)",
            self.port,
            self.tor_host,
            self.tor_port,
            self.strict,
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
                    host,
                    port,
                    ONION_SUFFIX,
                )
                client.sendall(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"
                )
                return
            try:
                upstream = socks.socksocket()
                upstream.set_proxy(
                    socks.SOCKS5,
                    self.tor_host,
                    self.tor_port,
                    rdns=True,
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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tor-host", default=DEFAULT_TOR_HOST)
    parser.add_argument("--tor-port", type=int, default=DEFAULT_TOR_PORT)
    parser.add_argument(
        "--strict/--no-strict",
        dest="strict",
        action=argparse.BooleanOptionalAction,
        default=True,
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