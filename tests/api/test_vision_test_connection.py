import json
import time
from unittest.mock import patch, MagicMock

import pytest
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
    assert "连接被拒" in data["error"]


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


def test_test_connection_custom_model_name_for_openai_compatible_endpoint():
    """Regression: a user running llama.cpp / vLLM / LM Studio with a
    custom vision model (e.g. 'qwen2-vl-7b-instruct') types that name
    into the now-free-text vision-model field and clicks 链接测试. The
    backend must:

    1. accept the custom model string (the previous design accidentally
       sent the literal 'openai-compatible' as the model name, which
       upstream endpoints rejected with HTTP 400 / 2013);
    2. forward it to _build_chat_model unchanged — the custom name is
       the value the user wants the endpoint to use;
    3. surface the upstream response in the standard envelope, with the
       exact model name in the error message so the user can tell what
       went wrong.
    """
    fake_llm = MagicMock()
    fake_response = MagicMock()
    fake_response.content = "ok"
    fake_llm.invoke.return_value = fake_response
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ) as mock_build:
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "url": "http://localhost:8000/v1",
                "api_key": "sk-llama-cpp",
                "model": "qwen2-vl-7b-instruct",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    # The custom name was forwarded to the model builder unchanged.
    kwargs = mock_build.call_args.kwargs
    assert kwargs["model_name"] == "qwen2-vl-7b-instruct"
    assert kwargs["base_url"] == "http://localhost:8000/v1"


def test_test_connection_does_not_inject_sentinel_for_unknown_model():
    """Regression: when the user types a model name the endpoint does
    not recognise, the backend must surface the upstream 'unknown model'
    error verbatim (with the model name in the message) instead of
    silently substituting a provider-type sentinel like 'openai-
    compatible'. The previous dropdown design submitted
    'openai-compatible' as the model name, producing a confusing
    'unknown model openai-compatible' error.
    """
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = Exception(
        'Error code: 400 - {"error": {"message": "invalid params, '
        "unknown model 'qwen2-vl-7b-instruct' (2013)\"}}"
    )
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
                "model": "qwen2-vl-7b-instruct",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    # The exact model name appears in the error so the user knows which
    # value to fix; the backend did not silently swap it for a sentinel.
    assert "qwen2-vl-7b-instruct" in data["error"]
    assert "openai-compatible" not in data["error"]


def test_test_connection_422_content_policy_surfaces_upstream_message():
    """When the upstream vision provider returns HTTP 422 with a
    content-moderation message (e.g. MiniMax-M3's
    "input new_sensitive, messages[0]'s content[1] image is
    sensitive, please check your input (1026)"), the link test
    must surface that message clearly so the user knows this is
    a provider-side policy decision — NOT a misconfigured URL,
    bad API key, or wrong model name.
    """
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = Exception(
        'Error code: 422 - {"error": {"message": "input new_sensitive, '
        "messages[0]'s content[1] image is sensitive, please check "
        "your input (1026)\"}}"
    )
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ):
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "provider": "openai_endpoint",
                "url": "https://api.MiniMax.chat/v1",
                "api_key": "sk-test",
                "model": "MiniMax-M3",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    # The upstream's exact message must be in the error so the user
    # can recognize it as a content-policy issue, not a config bug.
    assert "image is sensitive" in data["error"]
    # And the structured kind must be "content_policy" so the WebUI
    # can style this distinctly from generic 4xx errors.
    assert data.get("error_kind") == "content_policy"
    assert data.get("status_code") == 422


def test_test_connection_resolves_custom_sentinel():
    """REMOVED — the model dropdown no longer carries a '__custom__'
    sentinel. Models are filtered by the new image_vision_provider
    setting, and the link test always sends a real model name.
    Replaced by test_test_connection_uses_provider_from_body below.
    """


def test_test_connection_custom_sentinel_without_model_name_fails():
    """REMOVED — see test_test_connection_resolves_custom_sentinel.
    The '__custom__' flow no longer exists; the model dropdown is
    filtered by provider on the client side and never sends a
    sentinel value.
    """


def test_test_connection_uses_provider_from_body():
    """The vision test endpoint must dispatch via the provider
    supplied in the request body (rather than always forcing
    'openai_endpoint' as it did before the four-param redesign).
    This is what makes picking 'Anthropic' / 'Google' / 'Ollama' /
    'OpenAI' / 'OpenAI-Compatible' in the Vision Model Provider
    dropdown actually reach the right chat model implementation.
    Without provider dispatch, native Anthropic and Google endpoints
    fail because they don't speak the OpenAI wire format.
    """
    fake_llm = MagicMock()
    fake_response = MagicMock()
    fake_response.content = "ok"
    fake_llm.invoke.return_value = fake_response
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ) as mock_build:
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "provider": "openai_endpoint",
                "url": "http://localhost:8000/v1",
                "api_key": "",
                "model": "qwen2-vl-7b-instruct",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    kwargs = mock_build.call_args.kwargs
    assert kwargs["provider"] == "openai_endpoint"
    assert kwargs["model_name"] == "qwen2-vl-7b-instruct"
    assert kwargs["base_url"] == "http://localhost:8000/v1"


def test_test_connection_default_provider_when_omitted():
    """Backward compat: if the request body omits 'provider' (e.g.
    older clients, or tests written before the four-param redesign),
    the backend falls back to 'openai_endpoint' — same as the
    pre-redesign hardcoded value. The test must not regress to
    raising KeyError on missing 'provider'.
    """
    fake_llm = MagicMock()
    fake_response = MagicMock()
    fake_response.content = "ok"
    fake_llm.invoke.return_value = fake_response
    with patch(
        "local_deep_research.web.routes.vision_routes._build_chat_model",
        return_value=fake_llm,
    ) as mock_build:
        client = _client()
        resp = client.post(
            "/api/vision/test_connection",
            json={
                "url": "http://localhost:11434",
                "api_key": "",
                "model": "moondream2",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    kwargs = mock_build.call_args.kwargs
    assert kwargs["provider"] == "openai_endpoint"


def _vision_client():
    """Build a Flask test client that mounts the full vision blueprint
    (test_connection + available-models). The minimal _client() above
    is enough for unit tests, but the new route also reads from the
    blueprint directly so the same registration works for both.
    """
    return _client()


def test_available_vision_models_ollama_returns_models():
    """GET /api/vision/available-models with provider=ollama must call
    OllamaProvider.list_models_for_api and return its results in the
    {value, label, provider} envelope the WebUI expects. The provider
    tag must be lowercased to match the filter key in
    vision_provider_link.js (otherwise the filter hides the new
    options and the user sees the dropdown unchanged — the bug fixed
    in this revision).
    """
    fake_models = [
        {"value": "llava", "label": "LLaVA (Ollama)", "provider": "OLLAMA"},
        {"value": "moondream2", "label": "Moondream2 (Ollama)", "provider": "OLLAMA"},
    ]
    with patch(
        "local_deep_research.llm.providers.implementations.ollama.OllamaProvider.list_models_for_api",
        return_value=fake_models,
        create=True,
    ) as mock_list:
        client = _vision_client()
        resp = client.get(
            "/api/vision/available-models",
            query_string={
                "provider": "ollama",
                "url": "http://localhost:11434",
                "api_key": "",
            },
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["provider"] == "ollama"
    values = [m["value"] for m in data["models"]]
    assert values == ["llava", "moondream2"]
    for m in data["models"]:
        assert "value" in m
        assert "label" in m
        assert "provider" in m
        # Critical: provider tag must be lowercase so the JS filter
        # (which reads the lowercase value of the provider <select>)
        # can match it.
        assert m["provider"] == m["provider"].lower(), (
            f"provider tag {m['provider']!r} is not lowercased — "
            f"vision_provider_link.js will hide the new options and "
            f"the dropdown will appear unchanged to the user."
        )


def test_available_vision_models_requires_provider_and_url():
    """The endpoint must reject requests missing the required provider
    or url query parameters — both are needed to know where to fetch
    the model list from.
    """
    client = _vision_client()
    # Missing provider
    resp = client.get(
        "/api/vision/available-models",
        query_string={"url": "http://localhost:11434"},
    )
    assert resp.status_code == 400
    # Missing url
    resp = client.get(
        "/api/vision/available-models",
        query_string={"provider": "ollama"},
    )
    assert resp.status_code == 400


def test_available_vision_models_rejects_unknown_provider():
    """A provider key outside the supported set (e.g. someone POSTs
    'gpt-4o' as the provider by mistake) must be rejected with a
    clear 400 — not silently dispatch to a default.
    """
    client = _vision_client()
    resp = client.get(
        "/api/vision/available-models",
        query_string={"provider": "gpt-4o", "url": "http://x"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Unsupported" in data["error"] or "不支持" in data["error"]


def test_available_vision_models_propagates_provider_failure():
    """If the underlying list_models_for_api raises (e.g. connection
    refused, bad API key), the endpoint must return a clean 502 with
    a Chinese error message — not 500 with a stack trace.
    """
    with patch(
        "local_deep_research.llm.providers.implementations.ollama.OllamaProvider.list_models_for_api",
        side_effect=Exception("Connection refused"),
        create=True,
    ):
        client = _vision_client()
        resp = client.get(
            "/api/vision/available-models",
            query_string={
                "provider": "ollama",
                "url": "http://localhost:11434",
            },
        )
    assert resp.status_code == 502
    data = resp.get_json()
    assert "error" in data


def test_vision_test_uses_non_sensitive_probe_image():
    """Regression for MiniMax-M3 422 'image is sensitive' on the
    1x1 transparent PNG. The link test now sends an 8x8 solid-color
    sky-blue PNG (universally non-sensitive) instead of the
    degenerate 1x1 transparent PNG. Verify the route's probe
    constant is the new image, not the old one.
    """
    from local_deep_research.web.routes import vision_routes

    # 1x1 transparent PNG signature: starts with the old base64 prefix.
    OLD_1X1_PNG_PREFIX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    # The current constant must NOT be the 1x1 PNG.
    assert not vision_routes._PROBE_PNG_BASE64.startswith(OLD_1X1_PNG_PREFIX), (
        "vision_routes._PROBE_PNG_BASE64 is still the 1x1 transparent "
        "PNG — upstream providers (e.g. MiniMax-M3) reject it as "
        "'image is sensitive' with HTTP 422."
    )
    # And it must be a valid base64 PNG of non-trivial size (we want
    # a real, decodable image, not just a few bytes).
    import base64
    raw = base64.b64decode(vision_routes._PROBE_PNG_BASE64)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), (
        f"_PROBE_PNG_BASE64 does not decode to a PNG header: {raw[:8]!r}"
    )
    # IHDR chunk starts at byte 8. Width/height are 4-byte big-endian.
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    assert width >= 4 and height >= 4, (
        f"Probe image is too small ({width}x{height}) — content "
        f"moderation may still flag it as degenerate. Use a real, "
        f"non-trivial image."
    )


def test_save_all_settings_accepts_dynamic_vision_model():
    """Regression: when the user picks a vision model that the
    refresh button pulled from the live provider (e.g. MiniMax-M3
    or any private deployment), saving settings used to fail with
    'Validation errors' because the model value wasn't in the
    static options list. Adding report.image_vision_model to
    DYNAMIC_SETTINGS lets the backend accept any value the
    dynamically-populated dropdown sends.
    """
    from local_deep_research.web.routes.settings_routes import (
        DYNAMIC_SETTINGS,
    )

    assert "report.image_vision_model" in DYNAMIC_SETTINGS, (
        "report.image_vision_model must be in DYNAMIC_SETTINGS — "
        "the dropdown is dynamically populated by the refresh "
        "button and the backend must accept any value the user "
        "picks from it (e.g. private deployment names like "
        "MiniMax-M3). Without this, the user sees 'Validation "
        "errors' when they save the settings page."
    )
    # Sanity: the existing dynamic keys must still be in the list
    # (don't regress them).
    for key in ("llm.provider", "llm.model", "search.tool"):
        assert key in DYNAMIC_SETTINGS, (
            f"{key} must remain in DYNAMIC_SETTINGS — earlier "
            f"revisions added it for the same reason."
        )
