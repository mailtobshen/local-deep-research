"""Tests for VPN proxy reachability check."""
from unittest.mock import patch

from local_deep_research.security.vpn_precheck import (
    VPNCheckError,
    _parse_proxy_url,
)


def test_parse_proxy_url_http():
    host, port = _parse_proxy_url("http://172.25.128.1:10888")
    assert host == "172.25.128.1"
    assert port == 10888


def test_parse_proxy_url_socks5h():
    host, port = _parse_proxy_url("socks5h://proxy.example.com:1080")
    assert host == "proxy.example.com"
    assert port == 1080


def test_parse_proxy_url_invalid_empty_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("")


def test_parse_proxy_url_invalid_no_scheme_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("172.25.128.1:10888")


def test_parse_proxy_url_invalid_no_port_raises():
    import pytest
    with pytest.raises(VPNCheckError, match="Invalid proxy URL"):
        _parse_proxy_url("http://172.25.128.1")


def test_check_vpn_proxy_step1_port_unreachable():
    """TCP connect failure → VPNCheckError with 'port unreachable'."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    import socket as _socket

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection",
        side_effect=_socket.timeout("timed out"),
    ):
        import pytest
        with pytest.raises(VPNCheckError, match="port unreachable"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step1_connection_refused():
    """OSError (Connection refused) → VPNCheckError."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection",
        side_effect=ConnectionRefusedError("Connection refused"),
    ):
        import pytest
        with pytest.raises(VPNCheckError, match="port unreachable"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)