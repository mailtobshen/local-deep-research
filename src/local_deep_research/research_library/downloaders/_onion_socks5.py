"""Direct SOCKS5h fetch for .onion URLs.

Primary path for HTMLDownloader when the URL is a .onion host. Verified
2026-08-21: the in-process onion-connect-proxy (HTTP CONNECT via
127.0.0.1:18080) is unreliable on :443 — tor REP=0x1 / REP=0x5 in
roughly half the requests — while a fresh SOCKS5 handshake to
ldr-tor:9050 on :80 succeeds in 2-15s for the same hosts.

This helper replaces the proxy path entirely for .onion URLs. It
returns a `requests.Response`-shaped object so the rest of the
HTMLDownloader pipeline (rate tracking, OBS-G logging, extraction)
does not need to special-case this fetch path.

No new dependencies: the SOCKS5 handshake is hand-rolled over stdlib
``socket``. PySocks is intentionally NOT used because it would require
adding a new top-level dependency for a single fetch path.
"""

import socket
from typing import Optional
from urllib.parse import urlparse

import requests

# Container-internal addresses. See app_factory for the ldr-tor setup.
LDR_TOR_SOCKS_HOST = "ldr-tor"
LDR_TOR_SOCKS_PORT = 9050


class _Socks5Error(Exception):
    """Internal: anything that prevented a successful SOCKS5 fetch."""


def _socks5_connect(host: str, port: int, timeout: int) -> socket.socket:
    """Hand-rolled SOCKS5 client, returns a connected socket.

    Implements the minimum SOCKS5 protocol so we don't need PySocks:
    - greet (no auth)
    - CONNECT to host:port (domainname ATYP=0x03)
    - consume BND.ADDR + BND.PORT
    """
    if not (1 <= port <= 65535):
        raise _Socks5Error(f"invalid port: {port}")
    s = socket.create_connection(
        (LDR_TOR_SOCKS_HOST, LDR_TOR_SOCKS_PORT), timeout=timeout
    )
    try:
        s.sendall(bytes([0x05, 0x01, 0x00]))  # VER=5, NMETHODS=1, METHODS=NO_AUTH
        reply = s.recv(2)
        if reply != bytes([0x05, 0x00]):
            raise _Socks5Error(f"SOCKS5 greeting rejected: {reply.hex()}")
        host_b = host.encode("ascii", errors="replace")
        if len(host_b) > 255:
            raise _Socks5Error("host too long for SOCKS5 domainname ATYP")
        req = (
            bytes([0x05, 0x01, 0x00, 0x03, len(host_b)])
            + host_b
            + bytes([port >> 8, port & 0xFF])
        )
        s.sendall(req)
        s.settimeout(timeout)
        reply = s.recv(4)
        if len(reply) < 4:
            raise _Socks5Error(f"SOCKS5 reply truncated: {reply.hex()}")
        if reply[:2] != bytes([0x05, 0x00]):
            rep = reply[1]
            raise _Socks5Error(f"SOCKS5 CONNECT refused by tor (REP=0x{rep:02x})")
        atyp = reply[3]
        if atyp == 0x01:  # IPv4
            s.recv(4 + 2)
        elif atyp == 0x03:  # DOMAINNAME
            ln = s.recv(1)[0]
            s.recv(ln + 2)
        elif atyp == 0x04:  # IPv6
            s.recv(16 + 2)
        else:
            raise _Socks5Error(f"SOCKS5 ATYP unknown: 0x{atyp:02x}")
        return s
    except BaseException:
        try:
            s.close()
        except OSError:
            pass
        raise


def _decode_chunked(body: bytes) -> bytes:
    """Decode HTTP/1.1 chunked transfer-encoding.

    Format (per RFC 7230 §4.1):
        chunk-size CRLF
        chunk-data CRLF
        0 CRLF
        trailers CRLF
    Chunk-size is hex.
    """
    decoded = bytearray()
    i = 0
    while i < len(body):
        # Find end of chunk-size line.
        crlf = body.find(b"\r\n", i)
        if crlf < 0:
            break
        size_line = body[i:crlf]
        # Strip any chunk extensions after ';' (we don't use them).
        size_str = size_line.split(b";", 1)[0].strip()
        try:
            size = int(size_str, 16)
        except ValueError:
            # Garbage -- treat as raw remainder.
            decoded.extend(body[i:])
            break
        if size == 0:
            # End of chunked body; consume any trailing CRLF.
            break
        data_start = crlf + 2
        data_end = data_start + size
        if data_end > len(body):
            # Truncated; take what we have.
            decoded.extend(body[data_start:])
            break
        decoded.extend(body[data_start:data_end])
        # Skip trailing CRLF after the chunk data.
        i = data_end + 2
    return bytes(decoded)


def _parse_response(raw: bytes) -> requests.Response:
    """Build a requests.Response from raw HTTP/1.1 bytes over a tunnel.

    Sufficient for downstream extraction: status_code, headers,
    text, content, url, apparent_encoding. Handles the
    Transfer-Encoding: chunked response shape that DuckDuckGo /
    Facebook / most .onion sites use.
    """
    if b"\r\n\r\n" not in raw:
        raise _Socks5Error("HTTP response missing head/body separator")
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    if not lines:
        raise _Socks5Error("empty HTTP response")
    status_parts = lines[0].split(b" ", 2)
    if len(status_parts) < 2 or not status_parts[1].isdigit():
        raise _Socks5Error(f"unparseable status line: {lines[0][:80]!r}")
    from requests.structures import CaseInsensitiveDict
    resp = requests.Response()
    resp.status_code = int(status_parts[1])
    resp.url = ""  # set by caller
    resp.headers = CaseInsensitiveDict()
    for h in lines[1:]:
        if b":" not in h:
            continue
        k, _, v = h.partition(b":")
        resp.headers[k.strip().decode("latin-1", errors="replace")] = v.strip().decode(
            "latin-1", errors="replace"
        )
    # Decode chunked transfer-encoding if present.
    te = resp.headers.get("Transfer-Encoding", "")
    if "chunked" in te.lower():
        body = _decode_chunked(body)
    resp._content = body
    resp.encoding = resp.apparent_encoding
    return resp


def fetch_onion(url: str, timeout: int = 90) -> Optional[requests.Response]:
    """Direct SOCKS5h fetch for .onion URLs. Returns Response or None.

    Strategy:
      1. Parse URL, determine host + port (default 80).
      2. Open SOCKS5 connection to ldr-tor:9050.
      3. Send HTTP/1.1 GET over the tunnel.
      4. Read response, parse, return requests.Response.

    Only valid for .onion URLs. Returns None for clearnet / parsed
    failures / SOCKS5 failures / non-200 responses. The caller
    (HTMLDownloader._fetch_html) decides retry semantics.
    """
    if not url:
        return None
    try:
        u = urlparse(url)
    except (ValueError, AttributeError):
        return None
    host = (u.hostname or "").lower()
    if not host or host == "onion" or not host.endswith(".onion"):
        return None
    port = u.port or 80
    if not (1 <= port <= 65535):
        return None

    try:
        s = _socks5_connect(host, port, timeout)
    except _Socks5Error:
        return None
    except (socket.timeout, OSError):
        return None

    try:
        s.settimeout(timeout)
        path = u.path or "/"
        if u.query:
            path = f"{path}?{u.query}"
        # RFC 3986 says request-target must be ASCII; percent-encode
        # any non-ASCII characters (e.g. Chinese-language paths on
        # .onion sites). urllib.parse.quote with default safe="" is
        # too aggressive (would encode / and ?), so we use a safe set
        # that matches what requests does on the client side.
        try:
            from urllib.parse import quote
            path = quote(path, safe="/%?#:@!$&'()*+,;=-._~")
        except Exception:
            pass
        # Host should also be ASCII (.onion is) but be defensive.
        host_hdr = host.encode("ascii", errors="replace").decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_hdr}\r\n"
            f"User-Agent: ldr-onion-socks5/1.0\r\n"
            f"Accept: text/html,application/xhtml+xml,*/*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii", errors="replace")
        s.sendall(req)
        raw = b""
        while True:
            try:
                chunk = s.recv(4096)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            raw += chunk
    finally:
        try:
            s.close()
        except OSError:
            pass

    try:
        resp = _parse_response(raw)
    except _Socks5Error:
        return None
    resp.url = url
    if not (200 <= resp.status_code < 400):
        return None
    return resp
