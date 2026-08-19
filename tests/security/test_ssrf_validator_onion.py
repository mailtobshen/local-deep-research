"""Tests for ssrf_validator's handling of .onion URLs.

The darkweb pipeline routes .onion traffic through a local CONNECT proxy
(security.proxy_config.ONION_PROXY_URL -> Tor).  Because the kernel
resolver cannot resolve .onion hostnames, the validator's resolver-based
gate would otherwise reject every such URL before the proxy can take
over.  These tests pin the behaviour: .onion URLs pass; the surrounding
SSRF protections (private IPs, cloud metadata, scheme allow-list) still
hold.
"""
from __future__ import annotations

import socket

import pytest

from tests.test_utils import add_src_to_path

add_src_to_path()


from local_deep_research.security.ssrf_validator import validate_url  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        # Canonical v3 onion (DuckDuckGo).
        "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/",
        # Plain v3 onion over http.
        "http://kx5thpx2oluwml4w.onion/path",
        # v2-style 16-char onion (still routed through the same proxy).
        "http://exppyuzz4wqqyqhjn.onion/",
        # Upper-case host — validator must match case-insensitively.
        "http://EXAMPLE.ONION/",
    ],
)
def test_onion_url_passes_validator(url: str) -> None:
    """`.onion` URLs must be allowed — the proxy layer handles routing.

    Without this exemption, ``SafeSession.request`` raises ValueError at
    the SSRF gate before the request reaches the local CONNECT proxy,
    leaving source bodies empty (see DOC_SCHEDULER 0-byte finding).
    """
    assert validate_url(url) is True


def test_onion_url_passes_with_private_ip_flags_off() -> None:
    """`.onion` is allowed under the *default* SafeSession flags too.

    The downloader's ``SafeSession()`` is constructed without
    ``allow_private_ips=True``; the gate must not require that flag.
    """
    assert (
        validate_url(
            "http://kx5thpx2oluwml4w.onion/path",
            allow_localhost=False,
            allow_private_ips=False,
        )
        is True
    )


def test_onion_subdomain_trap_still_blocked() -> None:
    """`evil.onion.attacker.com` is a clearnet host — not a .onion URL.

    ``is_darkweb_url`` returns False for it (host = ``attacker.com``);
    the validator must keep treating it as a regular hostname.  If the
    host *resolves* to a public IP it passes; if it doesn't resolve it
    fails — exactly the same behaviour as before this exemption.
    """
    # We can't assert a specific True/False for this string because it
    # depends on real DNS.  The contract is: it is treated as a normal
    # hostname (i.e. is_darkweb_url returns False), not exempted as a
    # .onion URL.
    from local_deep_research.utilities.is_darkweb_url import is_darkweb_url

    assert is_darkweb_url("http://evil.onion.attacker.com/page") is False


def test_notonion_com_treated_as_normal_hostname() -> None:
    """`notonion.com` is a clearnet domain — the substring is irrelevant."""
    from local_deep_research.utilities.is_darkweb_url import is_darkweb_url

    assert is_darkweb_url("http://notonion.com/") is False


def test_localhost_still_blocked_when_onion_exemption_present() -> None:
    """The .onion gate must not weaken existing SSRF rules."""
    assert validate_url("http://127.0.0.1/x") is False


def test_rfc1918_still_blocked_when_onion_exemption_present() -> None:
    assert validate_url("http://10.0.0.5/") is False


def test_cloud_metadata_still_blocked_when_onion_exemption_present() -> None:
    assert (
        validate_url("http://169.254.169.254/latest/meta-data/") is False
    )


def test_onion_with_non_onion_scheme_still_blocked() -> None:
    """`.onion` only earns the exemption for http/https."""
    assert validate_url("ftp://kx5thpx2oluwml4w.onion/") is False
    assert validate_url("file:///etc/passwd") is False


def test_onion_url_does_not_attempt_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.onion` URLs must short-circuit before the kernel resolver runs.

    If the validator ever fell through to ``socket.getaddrinfo`` for a
    ``.onion`` URL, the call would raise ``socket.gaierror`` and the URL
    would be rejected — exactly the bug we are fixing.  This guard
    ensures the short-circuit stays in place.
    """
    calls: list[str] = []

    def _spy_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(
        "local_deep_research.security.ssrf_validator.socket.getaddrinfo",
        _spy_getaddrinfo,
    )

    assert (
        validate_url("http://kx5thpx2oluwml4w.onion/path") is True
    ), "validator must return True *before* the kernel resolver runs"
    assert calls == [], (
        f"getaddrinfo must not be called for .onion URLs; got: {calls}"
    )
