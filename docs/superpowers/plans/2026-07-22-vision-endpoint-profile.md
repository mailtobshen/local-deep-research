# Vision Endpoint Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-text `report.image_vision_model` with a 3-field vision endpoint profile (model dropdown + URL + API key) plus a one-click Test Connection endpoint that proves the configuration works.

**Architecture:** Add three new settings (`report.image_vision_model` select+custom, `report.image_vision_url`, `report.image_vision_api_key`). Extract `_build_chat_model(provider, model_name, base_url, api_key, settings_snapshot)` helper from `llm_config.get_llm` to make ChatOpenAI construction reusable. `VisionDescriber` accepts base_url/api_key and constructs a LangChain ChatOpenAI (OpenAI-compat — works for Ollama/OpenAI/OpenRouter/LM Studio/vLLM). New `POST /api/vision/test_connection` endpoint sends a 1x1 transparent PNG + "Reply with the single word: ok" to verify the configuration. Frontend adds a "Test Connection" button next to the URL field with green/red toast feedback. Backward-compatible: legacy text values in `report.image_vision_model` still work; if URL empty and model set, derive from `llm.ollama.url`.

**Tech Stack:** Python 3.14, LangChain `ChatOpenAI`, Flask blueprints, vanilla JS (no framework changes), pytest.

## Global Constraints

- **Backward compat**: `report.image_vision_model` already in admin DB keeps its value (string). New `report.image_vision_url` and `report.image_vision_api_key` default to `""`. If `report.image_vision_url` empty and model set, fall back to `llm.ollama.url`.
- **Drop-in replacement**: `VisionDescriber("llava")` (single positional arg, old API) still constructs a working describer.
- **Reuse existing pattern**: `report.image_vision_*` settings follow the same `password`/`text`/`select` patterns used by `llm.openai.api_key`, `llm.ollama.url`, `llm.model`.
- **Test connection semantics**: send a 1x1 transparent PNG + "Reply with the single word: ok" via ChatOpenAI. Catch all exceptions → return `success: false` with error message; never raise.
- **Test endpoint security**: `@login_required`. No SSRF protection (users legitimately test localhost Ollama, internal vLLM, etc).
- **i18n**: All new English descriptions get matching Chinese translations in `web/translations/zh.json` per the existing pattern (`i18n.t(setting.description)` in settings.js).
- **Test workflow**: write test on host under `tests/`, then `docker cp` → in-container pytest. Source hot-mounted into ldr-local.
- **Each task**: `git add` only that task's exact files; pre-existing dirty files (6 modified + 1 untracked on host) must not be swept in.

---

## File Structure

**Modified:**
- `src/local_deep_research/defaults/default_settings.json` — convert `report.image_vision_model` from text to select+options; add `report.image_vision_url` and `report.image_vision_api_key`.
- `src/local_deep_research/config/llm_config.py` — extract `_build_chat_model(provider, model_name, base_url, api_key, settings_snapshot=None)` helper.
- `src/local_deep_research/images/vision.py` — `VisionDescriber.__init__(model_name=None, base_url=None, api_key=None)`.
- `src/local_deep_research/web/services/research_service.py` — read 3 vision keys instead of 1 (around line 1101).
- `src/local_deep_research/web/translations/zh.json` — add Chinese descriptions for the 3 new settings + test button label + toast messages.
- `src/local_deep_research/web/routes/route_registry.py` — register new vision_routes blueprint.

**New:**
- `src/local_deep_research/web/routes/vision_routes.py` — `POST /api/vision/test_connection`.
- `src/local_deep_research/web/static/js/components/vision_test_button.js` — Test Connection button component + fetch handler.
- `tests/images/test_vision_describe.py` — unit tests for new VisionDescriber signature.
- `tests/api/test_vision_test_connection.py` — endpoint tests.

---

## Task 1: Convert `report.image_vision_model` to select with options + add URL/key

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json`

**Interfaces:**
- Produces: 3 new/updated setting entries used by later tasks.

- [ ] **Step 1: Locate the current `report.image_vision_model` entry**

Run: `grep -n '"report.image_vision_model"' src/local_deep_research/defaults/default_settings.json`

- [ ] **Step 2: Replace `report.image_vision_model` with select+options + add two sibling settings**

Open `defaults/default_settings.json`. Find the `"report.image_vision_model": { ... }` block (currently has `"ui_element": "text"`, no `options`, has `placeholder`). Replace the entire block + add two siblings immediately after:

```json
    "report.image_vision_model": {
        "category": "report_parameters",
        "description": "Vision-capable model name (e.g. gpt-4o, llava). Used to describe images that have no alt text. Pick from dropdown or type a custom name.",
        "editable": true,
        "max_value": null,
        "min_value": null,
        "name": "Vision Model",
        "options": [
            {"label": "GPT-4o (OpenAI)", "value": "gpt-4o"},
            {"label": "GPT-4o mini (OpenAI)", "value": "gpt-4o-mini"},
            {"label": "GPT-4 Turbo (OpenAI)", "value": "gpt-4-turbo"},
            {"label": "Claude 3 Opus (Anthropic)", "value": "claude-3-opus-20240229"},
            {"label": "Claude 3.5 Sonnet (Anthropic)", "value": "claude-3-5-sonnet-latest"},
            {"label": "Claude 3 Haiku (Anthropic)", "value": "claude-3-haiku-20240307"},
            {"label": "Gemini 1.5 Pro (Google)", "value": "gemini-1.5-pro"},
            {"label": "Gemini 1.5 Flash (Google)", "value": "gemini-1.5-flash"},
            {"label": "Qwen-VL-Max (Alibaba)", "value": "qwen-vl-max"},
            {"label": "Qwen-VL-Plus (Alibaba)", "value": "qwen-vl-plus"},
            {"label": "LLaVA (Ollama)", "value": "llava"},
            {"label": "LLaVA 13B (Ollama)", "value": "llava:13b"},
            {"label": "LLaVA-LLaMA3 (Ollama)", "value": "llava-llama3"},
            {"label": "BakLLaVA (Ollama)", "value": "bakllava"},
            {"label": "MiniCPM-V (Ollama)", "value": "minicpm-v"},
            {"label": "Moondream2 (Ollama)", "value": "moondream2"}
        ],
        "step": null,
        "type": "REPORT",
        "ui_element": "select",
        "value": "",
        "visible": true
    },
    "report.image_vision_url": {
        "category": "report_parameters",
        "description": "Base URL for the vision-capable model endpoint. Examples: http://localhost:11434 (Ollama), https://api.openai.com/v1, https://openrouter.ai/api/v1, http://localhost:1234/v1 (LM Studio). Leave blank to fall back to the main llm.ollama.url setting.",
        "editable": true,
        "max_value": null,
        "min_value": null,
        "name": "Vision Endpoint URL",
        "options": null,
        "placeholder": "http://localhost:11434",
        "step": null,
        "type": "REPORT",
        "ui_element": "text",
        "value": "",
        "visible": true
    },
    "report.image_vision_api_key": {
        "category": "report_parameters",
        "description": "API key for the vision endpoint. Leave blank for local Ollama. Required for OpenAI/Anthropic/OpenRouter/etc.",
        "editable": true,
        "max_value": null,
        "min_value": null,
        "name": "Vision API Key",
        "options": null,
        "step": null,
        "type": "REPORT",
        "ui_element": "password",
        "value": "",
        "visible": true
    }
```

- [ ] **Step 3: Validate JSON parses**

Run:
```bash
python3 -c "import json; d=json.load(open('src/local_deep_research/defaults/default_settings.json')); assert d['report.image_vision_model']['ui_element']=='select'; assert len(d['report.image_vision_model']['options'])==16; assert d['report.image_vision_url']['ui_element']=='text'; assert d['report.image_vision_api_key']['ui_element']=='password'; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Verify container hot-mount sees new schema**

Run:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
from sqlalchemy import inspect
from local_deep_research.database.session_context import get_user_db_session
from local_deep_research.database.models.settings import Setting
with get_user_db_session(username='admin', password='123456aB') as session:
    bind = session.get_bind()
    # Verify default settings registration path picks up new keys
    from local_deep_research.settings.manager import SettingsManager
    sm = SettingsManager(db_session=session)
    keys = sm.get_all_settings()
    found = [k for k in ['report.image_vision_url', 'report.image_vision_api_key'] if k in keys]
    print('found new keys:', found)
"
```
Expected: `found new keys: ['report.image_vision_url', 'report.image_vision_api_key']`.

If `SettingsManager.get_all_settings` doesn't expose defaults, fall back to importing `defaults.default_settings` directly:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
import json
d = json.load(open('/install/.venv/lib/python3.14/site-packages/local_deep_research/defaults/default_settings.json'))
assert d['report.image_vision_model']['ui_element'] == 'select'
assert len(d['report.image_vision_model']['options']) == 16
assert d['report.image_vision_url']['ui_element'] == 'text'
assert d['report.image_vision_api_key']['ui_element'] == 'password'
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/defaults/default_settings.json
git commit -m "feat(settings): convert vision_model to select+options + add url/key"
```

---

## Task 2: Extract `_build_chat_model` helper from `llm_config.get_llm`

**Files:**
- Modify: `src/local_deep_research/config/llm_config.py`
- Test: `tests/config/test_build_chat_model.py` (new)

**Interfaces:**
- Produces: `_build_chat_model(provider: str, model_name: str, base_url: Optional[str] = None, api_key: Optional[str] = None, settings_snapshot: Optional[dict] = None) -> BaseChatModel`. Returns a constructed LangChain chat model (uses `ChatOpenAI` for openai_endpoint/OpenAI/Ollama-via-OpenAI-compat). Raises if provider is unrecognized.

- [ ] **Step 1: Write the failing test**

Create `tests/config/test_build_chat_model.py`:

```python
from unittest.mock import patch, MagicMock
from local_deep_research.config.llm_config import _build_chat_model


def test_build_chat_model_openai_endpoint_with_base_url():
    with patch(
        "local_deep_research.config.llm_config.ChatOpenAI"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        llm = _build_chat_model(
            provider="openai_endpoint",
            model_name="llava",
            base_url="http://localhost:11434/v1",
            api_key="",
        )
    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model_name"] == "llava"
    assert kwargs["base_url"] == "http://localhost:11434/v1"
    # Empty string API key is normalized to None for some providers — just assert no exception
    assert llm is mock_instance


def test_build_chat_model_openai_provider():
    with patch(
        "local_deep_research.config.llm_config.ChatOpenAI"
    ) as mock_cls:
        _build_chat_model(
            provider="openai",
            model_name="gpt-4o",
            api_key="sk-test",
        )
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model_name"] == "gpt-4o"
    assert kwargs["api_key"] == "sk-test"


def test_build_chat_model_unknown_provider_raises():
    import pytest
    with pytest.raises(ValueError):
        _build_chat_model(provider="not-a-real-provider", model_name="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
mkdir -p tests/config
docker cp tests/config/test_build_chat_model.py ldr-local:/tmp/ldr_tests/test_build_chat_model.py
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_build_chat_model.py -v 2>&1 | tail -5'
```
Expected: FAIL with `ImportError: cannot import name '_build_chat_model'`.

- [ ] **Step 3: Add `_build_chat_model` helper to `llm_config.py`**

Open `src/local_deep_research/config/llm_config.py`. Read the existing `get_llm` function (around line 238+) to understand the construction patterns. Then add a new helper before `get_llm`:

```python
def _build_chat_model(
    provider: str,
    model_name: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    settings_snapshot: Optional[dict] = None,
) -> BaseChatModel:
    """Construct a LangChain chat model for a given provider.

    Vision and other callers use this helper to get a chat model with a
    specific base_url + api_key, without going through the full get_llm()
    path (which would also resolve temperature, token tracking, etc.).

    For openai_endpoint (which covers Ollama-via-OpenAI-compat, OpenRouter,
    LM Studio, vLLM, etc.), base_url is required to be non-empty.
    """
    provider = normalize_provider(provider or "")
    if provider in ("openai", "openai_endpoint"):
        return ChatOpenAI(
            model_name=model_name,
            base_url=base_url or None,
            api_key=api_key or None,
        )
    if provider == "anthropic":
        return ChatAnthropic(model=model_name, api_key=api_key or None)
    if provider == "ollama":
        # Ollama supports an OpenAI-compat endpoint at /v1.
        # Prefer explicit base_url; else fall back to llm.ollama.url.
        url = base_url
        if not url:
            from .thread_settings import get_setting_from_snapshot
            url = get_setting_from_snapshot(
                "llm.ollama.url",
                "http://localhost:11434",
                settings_snapshot=settings_snapshot,
            )
        # Use OpenAI-compat path so chat + vision both work.
        return ChatOpenAI(
            model_name=model_name,
            base_url=url.rstrip("/") + "/v1",
            api_key=api_key or "ollama",
        )
    raise ValueError(f"Unsupported provider for chat model: {provider!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command.
Expected: PASS (3/3).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/config/llm_config.py tests/config/test_build_chat_model.py
git commit -m "feat(llm): extract _build_chat_model helper for vision endpoint"
```

---

## Task 3: Update `VisionDescriber` to accept base_url and api_key

**Files:**
- Modify: `src/local_deep_research/images/vision.py`
- Test: `tests/images/test_vision_describe.py` (new)

**Interfaces:**
- Consumes: `_build_chat_model(provider, model_name, base_url, api_key, settings_snapshot)` from Task 2.
- Produces: `VisionDescriber(model_name=None, base_url=None, api_key=None)`. New keyword args are optional. Single positional arg still works (backward compat).

- [ ] **Step 1: Write the failing tests**

Create `tests/images/test_vision_describe.py`:

```python
from unittest.mock import patch, MagicMock
from local_deep_research.images import vision


def test_init_with_base_url_and_api_key_uses_openai_endpoint():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_llm = MagicMock()
        mock_build.return_value = mock_llm
        desc = vision.VisionDescriber(
            model_name="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
    mock_build.assert_called_once_with(
        provider="openai_endpoint",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        settings_snapshot=None,
    )
    assert desc.enabled is True


def test_init_empty_model_disables():
    desc = vision.VisionDescriber()
    assert desc.enabled is False
    assert desc._llm is None


def test_init_backward_compat_single_positional_arg():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_build.return_value = MagicMock()
        desc = vision.VisionDescriber("llava")
    mock_build.assert_called_once()
    assert desc.enabled is True
    # Old API didn't pass base_url/api_key — both default to None
    kwargs = mock_build.call_args.kwargs
    assert kwargs["model_name"] == "llava"
    assert kwargs["base_url"] is None
    assert kwargs["api_key"] is None


def test_init_uses_ollama_for_localhost_url():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_build.return_value = MagicMock()
        vision.VisionDescriber(
            model_name="llava",
            base_url="http://localhost:11434",
        )
    mock_build.assert_called_once_with(
        provider="openai_endpoint",
        model_name="llava",
        base_url="http://localhost:11434",
        api_key=None,
        settings_snapshot=None,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker cp tests/images/test_vision_describe.py ldr-local:/tmp/ldr_tests/test_vision_describe.py
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_vision_describe.py -v 2>&1 | tail -10'
```
Expected: at least 3 of 4 tests FAIL (signature change breaks old callers).

- [ ] **Step 3: Update VisionDescriber.__init__**

Open `src/local_deep_research/images/vision.py`. Replace the `__init__` method:

```python
    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.model_name = (model_name or "").strip()
        self._base_url = (base_url or "").strip() or None
        self._api_key = api_key or None
        self._llm = None
        if self.model_name:
            try:
                from ..config.llm_config import _build_chat_model

                # Decide provider from base_url heuristic:
                #   localhost / 127.0.0.1 / no base_url → openai_endpoint
                #     (covers Ollama-via-OpenAI-compat, LM Studio, vLLM).
                # For simplicity and to keep the public surface small,
                # always use openai_endpoint — ChatOpenAI works against
                # Ollama-via-/v1, OpenAI, OpenRouter, LM Studio, vLLM,
                # llama.cpp, etc.
                self._llm = _build_chat_model(
                    provider="openai_endpoint",
                    model_name=self.model_name,
                    base_url=self._base_url,
                    api_key=self._api_key,
                )
            except Exception:
                logger.exception(
                    "Failed to init vision LLM %s; fallback disabled",
                    self.model_name,
                )
                self._llm = None
```

The rest of `vision.py` (the `enabled` property and `describe()` method) stays unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command.
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/vision.py tests/images/test_vision_describe.py
git commit -m "feat(images): VisionDescriber accepts base_url + api_key"
```

---

## Task 4: Update research_service to read 3 vision keys + wire VisionDescriber

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py`

**Interfaces:**
- Consumes: `get_setting_from_snapshot(key, default, settings_snapshot)`; new `VisionDescriber(model_name, base_url, api_key)` from Task 3.

- [ ] **Step 1: Read the current code to find the vision wiring**

Open `src/local_deep_research/web/services/research_service.py` and locate the block around line 1101 that currently reads `report.image_vision_model` and constructs `vision = VisionDescriber(vision_model)`.

- [ ] **Step 2: Replace the vision wiring block**

Replace the existing block (1 line `vision_model = ...` + 1 line `vision = VisionDescriber(...)`) with:

```python
                        vision_model = get_setting_from_snapshot(
                            "report.image_vision_model",
                            "",
                            settings_snapshot=settings_snapshot,
                        )
                        vision_url = get_setting_from_snapshot(
                            "report.image_vision_url",
                            "",
                            settings_snapshot=settings_snapshot,
                        )
                        vision_key = get_setting_from_snapshot(
                            "report.image_vision_api_key",
                            "",
                            settings_snapshot=settings_snapshot,
                        )
                        # Backward compat: if URL empty but model set,
                        # fall back to the main Ollama endpoint.
                        if vision_model and not vision_url:
                            vision_url = get_setting_from_snapshot(
                                "llm.ollama.url",
                                "http://localhost:11434",
                                settings_snapshot=settings_snapshot,
                            )
                        vision = VisionDescriber(
                            model_name=vision_model,
                            base_url=vision_url or None,
                            api_key=vision_key or None,
                        )
```

The downstream `enhance_report_with_images(..., vision_model=vision_model, ...)` call needs to keep working. Inspect what the downstream expects. If it needs the model name string (not the VisionDescriber instance), change the call to pass `vision_model` (the string). The brief in Task 3 of the prior plan keeps `vision_model` as a string in the signature.

- [ ] **Step 3: Verify imports are correct**

Ensure `get_setting_from_snapshot` is already imported in `research_service.py` (it is, per the prior block). If `VisionDescriber` is not imported, add the import at the top of the file alongside other image imports.

- [ ] **Step 4: Run image test suite to verify no regression**

Run:
```bash
for f in tests/images/*.py; do docker cp "$f" ldr-local:/tmp/ldr_tests/$(basename "$f") 2>/dev/null; done
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest . -q 2>&1 | tail -3'
```
Expected: 138/138 still green.

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/services/research_service.py
git commit -m "feat(research): wire 3 vision keys (model+url+key) to VisionDescriber"
```

---

## Task 5: Add `POST /api/vision/test_connection` endpoint

**Files:**
- Create: `src/local_deep_research/web/routes/vision_routes.py`
- Modify: `src/local_deep_research/web/routes/route_registry.py`
- Test: `tests/api/test_vision_test_connection.py` (new)

**Interfaces:**
- Consumes: `_build_chat_model(provider, model_name, base_url, api_key)` from Task 2.
- Produces: Flask blueprint exposing `POST /api/vision/test_connection`. JSON body `{url, api_key, model}`. Returns JSON `{success: bool, response?: str, error?: str, status_code?: int, latency_ms?: int}`. HTTP 200 always (success/failure distinguished by `success` field); HTTP 401 if unauthenticated.

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/api/test_vision_test_connection.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
mkdir -p tests/api
docker cp tests/api/test_vision_test_connection.py ldr-local:/tmp/ldr_tests/test_vision_test_connection.py
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_vision_test_connection.py -v 2>&1 | tail -10'
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `vision_routes.py`**

Create `src/local_deep_research/web/routes/vision_routes.py`:

```python
"""POST /api/vision/test_connection — verify a vision endpoint config works.

Sends a 1x1 transparent PNG + "Reply with the single word: ok" through
the configured endpoint and reports whether the call succeeded. Useful
for users to validate their vision model + URL + API key before running
a full research.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from ...config.llm_config import _build_chat_model

logger = logging.getLogger(__name__)

# 1x1 transparent PNG. Minimal valid base64 image — does not need to be
# rendered by the model, just needs to be parseable.
_1X1_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# 1-char "vision probe" message. Asks for a 1-word reply so the call
# is fast and cheap (no actual image understanding needed; we just want
# to confirm the endpoint is reachable and the model accepts multimodal
# input).
_PROBE_TEXT = "Reply with the single word: ok"


vision_bp = Blueprint("vision", __name__)


@vision_bp.route("/test_connection", methods=["POST"])
def test_vision_connection():
    """Verify a vision endpoint is reachable and accepts multimodal input."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    api_key = body.get("api_key") or ""
    model = (body.get("model") or "").strip()

    if not url or not model:
        return jsonify(
            {
                "success": False,
                "error": "Both 'url' and 'model' are required.",
            }
        ), 200

    t0 = time.time()
    try:
        llm = _build_chat_model(
            provider="openai_endpoint",
            model_name=model,
            base_url=url,
            api_key=api_key,
        )

        # Build the multimodal probe message.
        from langchain_core.messages import HumanMessage

        msg = HumanMessage(
            content=[
                {"type": "text", "text": _PROBE_TEXT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_1X1_PNG_BASE64}"},
                },
            ]
        )
        response = llm.invoke([msg])
        content = getattr(response, "content", None) or str(response)
        latency_ms = int((time.time() - t0) * 1000)
        return jsonify(
            {
                "success": True,
                "response": str(content)[:200],
                "latency_ms": latency_ms,
            }
        ), 200
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        err = str(e) or type(e).__name__
        logger.info(f"Vision test_connection failed: {err}")
        # Try to extract status code from common error message shapes.
        status_code = None
        for needle in ("401", "403", "404", "429", "500", "502", "503"):
            if needle in err:
                status_code = int(needle)
                break
        return jsonify(
            {
                "success": False,
                "error": err,
                "status_code": status_code,
                "latency_ms": latency_ms,
            }
        ), 200
```

- [ ] **Step 4: Register the blueprint**

Open `src/local_deep_research/web/routes/route_registry.py`. Locate the section where other blueprints are registered. Add (or modify an existing pattern):

```python
        from .vision_routes import vision_bp
        app.register_blueprint(vision_bp, url_prefix="/api/vision")
```

Place it next to other blueprint registrations. If the file uses a different pattern (e.g. imports at top), follow that pattern.

- [ ] **Step 5: Run tests to verify they pass**

Run the same pytest command.
Expected: PASS (4/4).

- [ ] **Step 6: Commit**

```bash
git add src/local_deep_research/web/routes/vision_routes.py src/local_deep_research/web/routes/route_registry.py tests/api/test_vision_test_connection.py
git commit -m "feat(api): POST /api/vision/test_connection endpoint"
```

---

## Task 6: Frontend "Test Connection" button + i18n strings

**Files:**
- Create: `src/local_deep_research/web/static/js/components/vision_test_button.js`
- Modify: `src/local_deep_research/web/static/js/components/settings.js`
- Modify: `src/local_deep_research/web/translations/zh.json`

**Interfaces:**
- Produces: A "Test Connection" button rendered next to the `report.image_vision_url` input. Click → reads the three vision fields, posts to `/api/vision/test_connection`, shows toast.

- [ ] **Step 1: Add Chinese translations**

Add to `src/local_deep_research/web/translations/zh.json` (append at the end of the dict, before the closing `}`):

```json
  "Vision Model": "视觉模型",
  "Vision Endpoint URL": "视觉模型 API 地址",
  "Vision API Key": "视觉模型 API 密钥",
  "Test Connection": "测试连接",
  "Testing...": "测试中...",
  "Vision connected (%sms)": "视觉模型连接成功（%s 毫秒）",
  "Vision connection failed: %s": "视觉模型连接失败：%s"
```

- [ ] **Step 2: Validate JSON**

Run:
```bash
python3 -c "
import json
d = json.load(open('src/local_deep_research/web/translations/zh.json'))
for k in ['Vision Model', 'Vision Endpoint URL', 'Vision API Key', 'Test Connection', 'Testing...', 'Vision connected (%sms)', 'Vision connection failed: %s']:
    assert k in d, f'missing: {k}'
print('OK')
"
```
Expected: `OK`.

- [ ] **Step 3: Verify container hot-mount sees the translations**

Run:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
import json
d = json.load(open('/install/.venv/lib/python3.14/site-packages/local_deep_research/web/translations/zh.json'))
print('Vision Model:', d.get('Vision Model', 'MISSING'))
print('Test Connection:', d.get('Test Connection', 'MISSING'))
"
```
Expected: both print without MISSING.

- [ ] **Step 4: Create `vision_test_button.js`**

Create `src/local_deep_research/web/static/js/components/vision_test_button.js`:

```javascript
(function () {
    "use strict";

    /**
     * Attach a "Test Connection" button next to the vision URL field.
     * On click, reads the three vision fields and POSTs to /api/vision/test_connection.
     * Shows a toast with the result.
     */
    function attachTestButton(urlInput) {
        if (!urlInput) return;
        const row = urlInput.closest(".ldr-settings-row, .form-row, div");
        if (!row) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ldr-btn ldr-btn-secondary vision-test-btn";
        btn.textContent = i18n.t("Test Connection");
        btn.style.marginLeft = "0.5rem";
        btn.addEventListener("click", async function () {
            const apiKeyInput = document.querySelector(
                "input[name='report.image_vision_api_key']"
            );
            const modelInput = document.querySelector(
                "select[name='report.image_vision_model'], input[name='report.image_vision_model']"
            );
            const url = urlInput.value;
            const api_key = apiKeyInput ? apiKeyInput.value : "";
            const model = modelInput ? modelInput.value : "";
            btn.disabled = true;
            const originalText = btn.textContent;
            btn.textContent = i18n.t("Testing...");
            try {
                const resp = await fetch("/api/vision/test_connection", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url, api_key, model }),
                });
                const data = await resp.json();
                if (data.success) {
                    showAlert(
                        i18n.t("Vision connected (%sms)", data.latency_ms),
                        "success"
                    );
                } else {
                    showAlert(
                        i18n.t("Vision connection failed: %s", data.error),
                        "error"
                    );
                }
            } catch (e) {
                showAlert(
                    i18n.t("Vision connection failed: %s", String(e)),
                    "error"
                );
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        });
        // Insert after the URL input
        if (urlInput.parentElement) {
            urlInput.parentElement.appendChild(btn);
        } else {
            row.appendChild(btn);
        }
    }

    function showAlert(message, type) {
        // Reuse existing alert system if available; otherwise show a simple alert.
        if (typeof window.showAlert === "function") {
            window.showAlert(message, type);
        } else {
            alert(message);
        }
    }

    // Expose
    window.attachVisionTestButton = attachTestButton;
})();
```

- [ ] **Step 5: Hook the button into settings.js rendering**

Open `src/local_deep_research/web/static/js/components/settings.js`. Find where the default text input is rendered (around the change from `value="${...}"` to include `placeholder` in the prior plan). After that default branch, look for the function that processes all rendered settings. Find a stable post-render hook (e.g. after `inputElement` is appended, or in a callback after `requestAnimationFrame`). Add:

```javascript
        // Attach Test Connection button to vision URL field, if present.
        if (typeof window.attachVisionTestButton === "function") {
            const urlInput = document.querySelector(
                "input[name='report.image_vision_url']"
            );
            if (urlInput) {
                window.attachVisionTestButton(urlInput);
            }
        }
```

Place after the existing default branch and before any final cleanup. If the function is async (e.g. debounced search/refresh), make sure the call happens after the input is in the DOM.

- [ ] **Step 6: Load the new JS file**

Open `src/local_deep_research/web/templates/settings.html` (or whichever template includes settings.js). Add `<script src="{{ url_for('static', filename='js/components/vision_test_button.js') }}"></script>` after the settings.js script tag. If the settings page uses dynamic imports or a manifest, follow the same pattern.

If there's no settings.html template and components are loaded via JS bundler, follow the existing convention.

- [ ] **Step 7: Verify page loads + button appears**

Manual verification: open WebUI settings page, navigate to the vision settings, confirm a "测试连接" button appears next to the Vision Endpoint URL field. (Cannot be automated without browser test infra.)

- [ ] **Step 8: Commit**

```bash
git add src/local_deep_research/web/static/js/components/vision_test_button.js \
        src/local_deep_research/web/static/js/components/settings.js \
        src/local_deep_research/web/translations/zh.json \
        src/local_deep_research/web/templates/settings.html 2>/dev/null || true
git commit -m "feat(webui): Test Connection button for vision endpoint"
```

---

## Task 7: Full regression + i18n smoke

**Files:** none (verification only).

- [ ] **Step 1: Run full test suite**

Run:
```bash
for f in tests/images/*.py tests/research_library/downloaders/test_extraction_dispatcher.py tests/research_library/downloaders/test_extraction_pipeline.py tests/config/test_build_chat_model.py tests/api/test_vision_test_connection.py; do
  docker cp "$f" ldr-local:/tmp/ldr_tests/$(basename "$f") 2>/dev/null
done
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest . -q 2>&1 | tail -3'
```
Expected: count grows from 138 to 138 + 4 (build_chat_model) + 4 (vision_describe) + 4 (test_connection) = 150.

- [ ] **Step 2: Verify import smoke**

Run:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
from local_deep_research.images.vision import VisionDescriber
from local_deep_research.config.llm_config import _build_chat_model
from local_deep_research.web.routes.vision_routes import vision_bp
print('IMPORTS OK')
"
```
Expected: `IMPORTS OK`.

- [ ] **Step 3: Manual integration check**

In WebUI: open settings → find vision section → confirm 3 fields rendered (select with 16 options + URL text + password) → click Test Connection with `url=http://localhost:11434, model=llava` → green toast (or red if Ollama not running).

- [ ] **Step 4: Commit any test that surfaced regression (if any)**

```bash
git add tests/  # only if a missing regression test was created
git commit -m "test(vision): full-suite regression after endpoint profile"
```

---

## Self-Review (completed by plan author)

**Spec coverage**:
- 3 new settings: Task 1 ✅
- `_build_chat_model` extraction: Task 2 ✅
- VisionDescriber accepts base_url/api_key: Task 3 ✅
- research_service reads 3 keys: Task 4 ✅
- POST /api/vision/test_connection: Task 5 ✅
- Frontend Test Connection button + i18n: Task 6 ✅
- Backward compat (legacy text values, fallback to llm.ollama.url): Task 3 (single-positional-arg test) + Task 4 (fallback logic) ✅
- i18n Chinese translations: Task 6 ✅
- Full regression: Task 7 ✅

**Placeholder scan**: No TBD/TODO. Every code block is verbatim. Test code complete.

**Type consistency**:
- `_build_chat_model(provider, model_name, base_url=None, api_key=None, settings_snapshot=None)` defined in Task 2, consumed identically in Tasks 3 and 5.
- `VisionDescriber(model_name=None, base_url=None, api_key=None)` defined in Task 3, consumed in Task 4.
- `get_setting_from_snapshot(key, default, settings_snapshot)` used identically in Tasks 1 (none — defaults), 4 (3 calls), and follows existing pattern.
- `POST /api/vision/test_connection` body keys (`url`, `api_key`, `model`) and response keys (`success`, `response`, `error`, `latency_ms`, `status_code`) consistent across Tasks 5 and 6.

**Risk**: Task 6 (frontend button injection) is the most fragile. If `settings.js` doesn't have a clean post-render hook, may need iteration. Mitigation: explicit "If the function is async, make sure the call happens after the input is in the DOM" note.