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


def test_check_vpn_proxy_step2_url_error_raises():
    """Step 1 OK + step 2 URLError → VPNCheckError 'cannot reach external'."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock
    import urllib.error

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.URLError("Name or service not known")
        mock_opener_factory.return_value = mock_opener

        import pytest
        with pytest.raises(VPNCheckError, match="cannot reach external network"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step2_bad_status_raises():
    """Step 2 returns HTTP 500 → VPNCheckError."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        import pytest
        with pytest.raises(VPNCheckError, match="HTTP 500"):
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)


def test_check_vpn_proxy_step2_success_returns_none():
    """Both steps OK → returns None (no exception)."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        # Should not raise
        result = check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert result is None


def test_check_vpn_proxy_step2_accepts_status_200():
    """Status 200 also accepted (some proxies rewrite 204 → 200)."""
    from local_deep_research.security.vpn_precheck import check_vpn_proxy
    from unittest.mock import patch, MagicMock

    with patch(
        "local_deep_research.security.vpn_precheck.socket.create_connection"
    ), patch(
        "local_deep_research.security.vpn_precheck.urllib.request.build_opener"
    ) as mock_opener_factory:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_factory.return_value = mock_opener

        result = check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert result is None

class TestTransientRetry:
    """2026-08-26: preflight often hit the VPN error on transient
    network blips — user decision: retry 3 times before surfacing."""

    def test_transient_failure_then_success_no_error(self):
        """Fail twice (transient), succeed on 3rd → no VPNCheckError."""
        from local_deep_research.security.vpn_precheck import check_vpn_proxy
        import socket as _socket
        import urllib.request as _ur

        class FakeResp:
            status = 204

        def flaky_open(req, timeout=None):
            flaky_open.calls += 1
            if flaky_open.calls < 3:
                raise OSError("transient blip")
            return FakeResp()
        flaky_open.calls = 0

        with (
            patch(
                "local_deep_research.security.vpn_precheck.socket"
                ".create_connection",
                return_value=__import__("contextlib").nullcontext(),
            ),
            patch.object(
                _ur.OpenerDirector, "open", side_effect=flaky_open
            ),
        ):
            # must not raise
            check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert flaky_open.calls == 3

    def test_all_three_attempts_fail_raises(self):
        """Persistent failure across all retries → VPNCheckError."""
        from local_deep_research.security.vpn_precheck import check_vpn_proxy
        import socket as _socket
        import urllib.request as _ur

        def always_fail(req, timeout=None):
            always_fail.calls += 1
            raise OSError("down")
        always_fail.calls = 0

        with (
            patch(
                "local_deep_research.security.vpn_precheck.socket"
                ".create_connection",
                return_value=__import__("contextlib").nullcontext(),
            ),
            patch.object(
                _ur.OpenerDirector, "open", side_effect=always_fail
            ),
        ):
            import pytest
            with pytest.raises(VPNCheckError, match="cannot reach"):
                check_vpn_proxy("http://172.25.128.1:10888", timeout=1.0)
        assert always_fail.calls == 3
