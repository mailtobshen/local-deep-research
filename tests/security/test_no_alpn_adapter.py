"""ALPN suppression for hosts whose bot gate fingerprints the ClientHello.

Baidu Baike (baike.baidu.com) serves a "百度安全验证" challenge page with
HTTP 403 to any TLS ClientHello carrying an ALPN extension. Isolated by
single-variable probe: same exit IP, same User-Agent, same headers, same
TLS version — toggling ALPN alone flipped 200 (1,264,313 B real page) vs
403 (2,839 B challenge), reproducibly across 4 alternating rounds.

urllib3 calls ``context.set_alpn_protocols(ALPN_PROTOCOLS)``
unconditionally inside ``ssl_wrap_socket`` (util/ssl_.py), *after* any
caller-supplied ssl_context is selected. So handing it a plain context is
not enough — the context itself has to refuse the call.

These tests use a real local TLS server: the server offers ALPN, so the
server-side ``selected_alpn_protocol()`` is a direct, non-mocked readout
of whether the client offered it in its ClientHello.
"""
from __future__ import annotations

import datetime
import socket
import ssl
import threading

import pytest

from local_deep_research.security.safe_requests import SafeSession


def _self_signed(tmp_path):
    """Return (certfile, keyfile) for a localhost self-signed cert."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


class _ALPNRecordingServer:
    """One-shot HTTPS server recording the client's offered ALPN.

    ``offered`` is the protocol the server negotiated: None means the
    client sent no ALPN extension at all, which is exactly the property
    under test.
    """

    def __init__(self, certfile: str, keyfile: str):
        self.offered: str | None = None
        self.handled = threading.Event()
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile, keyfile)
        # Server advertises ALPN; if the client offers it too, the
        # handshake selects it and selected_alpn_protocol() is non-None.
        self._ctx.set_alpn_protocols(["http/1.1"])
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
            with conn:
                try:
                    tls = self._ctx.wrap_socket(conn, server_side=True)
                except ssl.SSLError:
                    return
                with tls:
                    self.offered = tls.selected_alpn_protocol()
                    try:
                        tls.recv(65536)
                        tls.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Length: 2\r\n"
                            b"Connection: close\r\n\r\nok"
                        )
                    except OSError:
                        pass
        finally:
            self.handled.set()
            self._sock.close()


@pytest.fixture
def alpn_server(tmp_path):
    certfile, keyfile = _self_signed(tmp_path)
    server = _ALPNRecordingServer(certfile, keyfile)
    server.start()
    yield server, certfile
    server.handled.wait(timeout=5)


def _get(session, port, certfile):
    return session.get(
        f"https://localhost:{port}/", verify=certfile, timeout=10
    )


def test_whitelisted_host_sends_no_alpn(alpn_server, monkeypatch):
    """A host on the no-ALPN whitelist must produce a ClientHello with no
    ALPN extension — the server therefore negotiates nothing.
    """
    server, certfile = alpn_server
    session = SafeSession(allow_localhost=True)
    # Treat this test server as if it were a whitelisted host.
    monkeypatch.setattr(
        "local_deep_research.security.safe_requests._NO_ALPN_HOST_SUFFIXES",
        ("localhost",),
    )
    resp = _get(session, server.port, certfile)

    assert resp.status_code == 200
    server.handled.wait(timeout=5)
    assert server.offered is None, (
        "client still offered ALPN; Baidu's gate would 403 this request"
    )


def test_non_whitelisted_host_keeps_alpn(alpn_server, monkeypatch):
    """ALPN suppression must NOT leak to other hosts — everything else
    keeps urllib3's default so HTTP/2 negotiation stays possible.
    """
    server, certfile = alpn_server
    session = SafeSession(allow_localhost=True)
    monkeypatch.setattr(
        "local_deep_research.security.safe_requests._NO_ALPN_HOST_SUFFIXES",
        ("example.invalid",),
    )
    resp = _get(session, server.port, certfile)

    assert resp.status_code == 200
    server.handled.wait(timeout=5)
    assert server.offered == "http/1.1"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://baike.baidu.com/item/x", True),
        ("https://www.baidu.com/", True),
        ("https://BAIKE.BAIDU.COM/item/x", True),
        ("https://example.com/", False),
        # Suffix match must be on a domain boundary, not a substring.
        ("https://notbaidu.com/", False),
        ("https://baidu.com.evil.test/", False),
    ],
)
def test_whitelist_matching_is_domain_bounded(url, expected):
    from local_deep_research.security.safe_requests import _needs_no_alpn

    assert _needs_no_alpn(url) is expected
