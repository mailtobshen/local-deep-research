"""设置页「测试暗网连接」端点。

端点在探测失败时仍返回 HTTP 200 —— 探测结果本身就是要展示的内容,
失败不是请求错误。前端据 status 字段渲染成功/失败。
"""
from contextlib import contextmanager
from unittest.mock import Mock, patch

from flask import Flask

from local_deep_research.web.routes.settings_routes import settings_bp


def _create_test_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["WTF_CSRF_ENABLED"] = False
    app.register_blueprint(settings_bp)

    @app.errorhandler(500)
    def _handle_500(error):
        return {"error": "Internal server error"}, 500

    return app


@contextmanager
def _authenticated_client(app):
    """Bypass @login_required by faking db_manager.is_user_connected."""
    mock_db = Mock()
    mock_db.is_user_connected.return_value = True
    with patch(
        "local_deep_research.web.auth.decorators.db_manager", mock_db
    ):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "testuser"
            yield client


def test_endpoint_returns_probe_result():
    """探测成功: 返回 ok + detail + latency_ms."""
    app = _create_test_app()
    with _authenticated_client(app) as client:
        with patch(
            "local_deep_research.web.routes.settings_routes.probe_darkweb",
            return_value=Mock(
                status="ok",
                detail="L4: 取回 7 条 .onion 结果",
                latency_ms=4200,
            ),
        ):
            resp = client.post("/settings/api/test-darkweb")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["detail"].startswith("L4:")
    assert body["latency_ms"] == 4200


def test_endpoint_reports_failure_without_http_error():
    """探测失败仍是 200 —— 失败详情是正常响应内容,不是请求错误。"""
    app = _create_test_app()
    with _authenticated_client(app) as client:
        with patch(
            "local_deep_research.web.routes.settings_routes.probe_darkweb",
            return_value=Mock(
                status="error",
                detail=(
                    "L2: SearXNG 未启用 ahmia/torch — "
                    "引擎块尚未合入 searxng/settings.yml"
                ),
                latency_ms=0,
            ),
        ):
            resp = client.post("/settings/api/test-darkweb")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert "L2:" in body["detail"]