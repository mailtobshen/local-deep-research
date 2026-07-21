import json
import time
from unittest.mock import patch, MagicMock

from flask import Flask
from flask.testing import FlaskClient

from local_deep_research.web.routes.vision_routes import vision_bp


def _client():
    app = Flask(__name__)
    app.register_blueprint(vision_bp, url_prefix="/api/vision")
    return app.test_client()


def test_test_connection_success():
    fake_llm = MagicMock()
    fake_response = MagicMock()
    fake_response.content = "ok"
    fake_llm.invoke.return_value = fake_response
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ):
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "url": "http://localhost:11434",
                "api_key": "",
                "model": "llava",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "latency_ms" in data


def test_test_connection_auth_failure_returns_success_false():
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = Exception("401 Unauthorized")
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ):
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "url": "http://x",
                "api_key": "bad",
                "model": "gpt-4o",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "401" in data["error"]


def test_test_connection_network_error_returns_success_false():
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        side_effect=Exception("Connection refused"),
    ):
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "url": "http://nonexistent:1234",
                "api_key": "",
                "model": "llava",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "Connection refused" in data["error"]


def test_test_connection_missing_fields():
    client = _client()
    resp = client.post(
        "/api/vision/test_connection",
        json={"url": "http://x"},  # missing api_key and model
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "model" in data["error"].lower() or "url" in data["error"].lower()
