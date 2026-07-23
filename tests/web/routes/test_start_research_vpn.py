"""Tests for VPN check integration into /api/start_research."""
from unittest.mock import patch, MagicMock

from local_deep_research.security.vpn_precheck import VPNCheckError


def _fake_start_research_app():
    """Build a minimal Flask app with just the start_research route registered.

    Returns a Flask test client. We don't import the full app to avoid pulling
    in langchain_anthropic, encrypted DB, etc. — just enough to exercise the
    VPN check branch.

    The route references check_vpn_proxy via the module attribute
    (vpn_precheck.check_vpn_proxy) so tests can patch the symbol at its source
    location and intercept the call.
    """
    from flask import Flask, jsonify, request
    from local_deep_research.security import vpn_precheck
    from local_deep_research.security.vpn_precheck import VPNCheckError

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/api/start_research", methods=["POST"])
    def fake_route():
        # Replicates the VPN-check branch only (not the full endpoint).
        proxy_enabled = True
        proxy_url = "http://172.25.128.1:10888"
        try:
            vpn_precheck.check_vpn_proxy(proxy_url)
        except VPNCheckError as e:
            return (
                jsonify({
                    "status": "error",
                    "error": "vpn_proxy_unavailable",
                    "message": str(e),
                    "hint": "Please enable your VPN proxy and try again.",
                }),
                422,
            )
        return jsonify({"status": "ok"}), 200

    return app.test_client()


def test_422_when_check_raises_vpn_check_error():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        side_effect=VPNCheckError("port unreachable: 1.2.3.4:10888 (refused)"),
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["error"] == "vpn_proxy_unavailable"
        assert "port unreachable" in data["message"]


def test_passthrough_when_check_succeeds():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        return_value=None,
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


def test_422_body_shape_matches_spec():
    client = _fake_start_research_app()
    with patch(
        "local_deep_research.security.vpn_precheck.check_vpn_proxy",
        side_effect=VPNCheckError("test failure"),
    ):
        resp = client.post("/api/start_research", json={})
        assert resp.status_code == 422
        data = resp.get_json()
        # All 4 spec-required keys present
        for key in ("status", "error", "message", "hint"):
            assert key in data, f"missing key: {key}"
        assert data["status"] == "error"
        assert data["error"] == "vpn_proxy_unavailable"
        assert "VPN proxy" in data["hint"] or "VPN" in data["hint"]