# Vision Endpoint Profile — 设计

**Date:** 2026-07-22
**Branch:** master
**Builds on:**
- 2026-07-20 image subsystem (commit `573d7d73`+): ImageBank/VisionDescriber
- 2026-07-21 SearXNG image support (commit `b51a611d`): report.enable_images gate
- 2026-07-21 unified fetcher (commit `f98a261d`): image extraction flow

## Goal

Replace the single-text `report.image_vision_model` setting with a **3-field vision endpoint profile** (model + URL + API key), so users can:
1. Pick a vision model from a dropdown (no more free-text typos)
2. Configure a dedicated endpoint URL (independent of main LLM provider)
3. Provide the API key inline
4. Verify the connection with a one-click "Test Connection" button

End state: a user can fully configure vision fallback in <30 seconds and **verify** it works without running a full research.

## Root Cause (verified)

`report.image_vision_model` (text) lets users type anything. Validation is impossible — common failures:
- Typo in model name (`gtp-4o` vs `gpt-4o`)
- Model name not supported by the current `llm.provider` (user fills `gpt-4o` but provider is Ollama)
- Vision call hits wrong endpoint URL (Ollama vs OpenAI base URLs differ)

VisionDescriber silently returns `None` on failure → no images get alt text → only alt-less images get dropped → report has fewer images than possible.

## Unified Gating Invariant

`report.enable_images=true` (master) AND all three new vision settings populated → vision fallback runs.

| `enable_images` | vision model | vision URL | vision key | behavior |
|---|---|---|---|---|
| false | (any) | (any) | (any) | text-only report (current behavior) |
| true | empty | (any) | (any) | no vision fallback (vision disabled) |
| true | set | empty | empty | Ollama local default (http://localhost:11434, no key) |
| true | set | set | set | openai-compat endpoint, with key |
| true | set (legacy text value) | (any) | (any) | backward compat: treat as model name, derive URL/key from main llm.* provider |

## Architecture & Data Flow

```
WebUI Settings → 3 new vision keys
  ↓
research_service.py: enhance_report_with_images() reads 3 keys from snapshot
  ↓
VisionDescriber.__init__(model_name, base_url, api_key)
  ↓
Constructs LangChain ChatOpenAI(base_url=..., api_key=...) — works for OpenAI/OpenRouter/Ollama-via-OpenAI-compat
  ↓
describe(image_url) → base64 encode + HumanMessage(image_url=...) → 30-char Chinese alt
  ↓
Backfill into ImageBank → second-pass LLM picks images

New endpoint: POST /api/vision/test_connection
  Body: {url, api_key, model}
  Logic: send 1x1 transparent PNG + "Reply with the single word: ok" → return success/error
  UI: button next to URL field, green/red toast on result
```

## Components & Interfaces

### New settings (in `defaults/default_settings.json`)

```json
"report.image_vision_model": {
  "category": "report_parameters",
  "name": "Vision Model",
  "description": "Vision-capable model name (e.g. gpt-4o, llava). Used to describe images that have no alt text. Pick from dropdown or type a custom name.",
  "ui_element": "select",
  "type": "REPORT",
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
  "value": "",
  "editable": true,
  "visible": true
},
"report.image_vision_url": {
  "category": "report_parameters",
  "name": "Vision Endpoint URL",
  "description": "Base URL for the vision-capable model endpoint. Examples: http://localhost:11434 (Ollama), https://api.openai.com/v1, https://openrouter.ai/api/v1, http://localhost:1234/v1 (LM Studio). Leave blank to fall back to the main llm.ollama.url setting.",
  "ui_element": "text",
  "type": "REPORT",
  "value": "",
  "editable": true,
  "visible": true,
  "placeholder": "http://localhost:11434"
},
"report.image_vision_api_key": {
  "category": "report_parameters",
  "name": "Vision API Key",
  "description": "API key for the vision endpoint. Leave blank for local Ollama. Required for OpenAI/Anthropic/OpenRouter/etc.",
  "ui_element": "password",
  "type": "REPORT",
  "value": "",
  "editable": true,
  "visible": true
}
```

### New endpoint: `POST /api/vision/test_connection`

**Location**: `src/local_deep_research/web/routes/vision_routes.py` (new file)

**Request body** (JSON):
```json
{
  "url": "http://localhost:11434",
  "api_key": "",
  "model": "llava"
}
```

**Response** (JSON, 200 always — see `success` field):
```json
{"success": true, "response": "ok", "latency_ms": 1234}
```
or
```json
{"success": false, "error": "Connection refused", "status_code": null}
```
or
```json
{"success": false, "error": "401 Unauthorized: invalid api key", "status_code": 401}
```

**Implementation**: construct a `ChatOpenAI(base_url=url, api_key=api_key, model_name=model, timeout=30)`, send a HumanMessage with a 1x1 transparent PNG (base64) + text `"Reply with the single word: ok"`. Catch `Exception` → return `success: false`.

**Auth**: `@login_required` (same as other settings API).

### VisionDescriber (modified)

```python
class VisionDescriber:
    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = (model_name or "").strip()
        self._base_url = (base_url or "").strip() or None
        self._api_key = api_key or None
        if self.model_name:
            try:
                from ..config.llm_config import _build_chat_model
                self._llm = _build_chat_model(
                    provider="openai_endpoint",  # OpenAI-compat
                    model_name=self.model_name,
                    base_url=self._base_url,
                    api_key=self._api_key,
                )
            except Exception:
                logger.exception("Failed to init vision LLM; fallback disabled")
                self._llm = None
```

`_build_chat_model` is a new helper that returns a LangChain `BaseChatModel` given (provider, model, base_url, api_key) — extracted from `llm_config.get_llm` to make it reusable.

### research_service.py (modified, ~5 lines)

Change at `research_service.py:1101-1119`:
```python
vision_model = get_setting_from_snapshot(
    "report.image_vision_model", "", settings_snapshot=settings_snapshot
)
vision_url = get_setting_from_snapshot(
    "report.image_vision_url", "", settings_snapshot=settings_snapshot
)
vision_key = get_setting_from_snapshot(
    "report.image_vision_api_key", "", settings_snapshot=settings_snapshot
)
# Backward compat: if URL empty and model set, derive from main llm.ollama.url
if vision_model and not vision_url:
    vision_url = get_setting_from_snapshot(
        "llm.ollama.url", "http://localhost:11434", settings_snapshot=settings_snapshot
    )
vision = VisionDescriber(
    model_name=vision_model,
    base_url=vision_url or None,
    api_key=vision_key or None,
)
```

### WebUI "Test Connection" button (in settings.js)

- **Placement**: rendered to the right of the `report.image_vision_url` input field
- **Behavior on click**:
  1. Collect `url`, `api_key`, `model` from the three vision fields
  2. `POST /api/vision/test_connection` with JSON body
  3. Show toast: green ✓ "Vision connected (1234ms)" or red ✗ with error message
- **Disabled while in flight** (prevent double-clicks)
- **Loading state**: spinner on button while request in flight

## Error Handling & Boundaries

| Stage | Failure | Handling |
|---|---|---|
| Test endpoint — network | Connection refused / timeout | return `success: false, error: <message>` |
| Test endpoint — auth | 401/403 | return `success: false, error: <api response>, status_code: 401` |
| Test endpoint — bad model | 400 / 404 | return `success: false, error: <api response>` |
| Test endpoint — rate limit | 429 | return `success: false, error: "rate limited", status_code: 429` |
| `VisionDescriber.__init__` — any | Exception | log + `enabled=False`; no vision fallback |
| `VisionDescriber.describe` — network/HTTP | Exception | return `None` (current behavior) |
| `VisionDescriber.describe` — empty vision_url with model | not raised | falls back to `llm.ollama.url` |

**Security**:
- API key in `report.image_vision_api_key` stored encrypted (SQLCipher) — same as other password settings
- Test endpoint: requires login (no anonymous probing of arbitrary endpoints)
- No SSRF protection on the test endpoint itself (intentional — users may legitimately test localhost Ollama, internal self-hosted vLLM, etc.)

**Key boundaries**:
1. **Empty vision_model**: vision disabled (current behavior).
2. **Empty vision_url with vision_model set**: derive from `llm.ollama.url` (Ollama default `http://localhost:11434`).
3. **Legacy text value in `report.image_vision_model`**: backward compat — string value still works as model name.
4. **Settings UI: select with allowCustom=true** (already supported by settings.js for `llm.model`).

## Testing Strategy

### New unit tests

**`tests/images/test_vision_describe.py`** (new):
- `VisionDescriber(model="gpt-4o", base_url="http://x", api_key="k")` constructs ChatOpenAI with these args (mock and verify)
- Empty model → `enabled=False`, `_llm=None`
- Empty base_url → `_base_url=None`
- Backward compat: `VisionDescriber("llava")` (single arg, old API) still works

**`tests/api/test_vision_test_connection.py`** (new):
- `POST /api/vision/test_connection` with valid Ollama-style config → 200 success
- Network error → 200 with `success: false, error: "..."`
- Auth failure (mock 401 response) → 200 with `success: false, status_code: 401`
- Unauthenticated request → 401

### New frontend test

`tests/web/test_vision_test_button.js` (or inline in settings.test.js):
- Button is rendered next to `report.image_vision_url`
- Click → fetch `/api/vision/test_connection` with current values
- Success toast on 200+success
- Error toast on 200+success:false

### Existing tests

- `tests/images/test_models.py::test_settings_registered` — already updated to `value is True` for `enable_images`. Vision model tests not affected.
- All 138 existing tests still pass.

### Manual integration

1. Start ldr-local, log in
2. Set `report.image_vision_model = llava`, `report.image_vision_url = http://localhost:11434`
3. Click "Test Connection" → green toast
4. Run a research → vision fallback runs for alt-less images

## Changed / New Files

**New**:
- `src/local_deep_research/web/routes/vision_routes.py` — `POST /api/vision/test_connection`
- `tests/images/test_vision_describe.py`
- `tests/api/test_vision_test_connection.py`

**Modified**:
- `src/local_deep_research/defaults/default_settings.json` — `report.image_vision_model` (text → select+options); add `report.image_vision_url`, `report.image_vision_api_key`
- `src/local_deep_research/images/vision.py` — VisionDescriber accepts base_url/api_key
- `src/local_deep_research/config/llm_config.py` — extract `_build_chat_model(provider, model_name, base_url, api_key, settings_snapshot)` helper
- `src/local_deep_research/web/services/research_service.py:1101-1119` — read 3 vision keys
- `src/local_deep_research/web/routes/route_registry.py` — register new blueprint
- `src/local_deep_research/web/static/js/components/settings.js` — Test Connection button + fetch handler
- `src/local_deep_research/web/translations/zh.json` — 3 new setting names + descriptions + test button label + toast messages

## YAGNI

- No multi-profile (user has 1 vision endpoint)
- No vision call caching
- No test history/log
- No retry/timeout configuration for vision calls (use LangChain defaults)
- No SSRF protection on test endpoint (user may legitimately test localhost)
- No OAuth/refresh-token flow (just static API keys)

## Caller Pre-Verification (verified 2026-07-22)

- `report.image_vision_model` consumers: `images/vision.py:14` and `web/services/research_service.py:1101`. Single writer at construction; will read from snapshot.
- `images/vision.py:VisionDescriber.__init__` signature change is backward-compatible (all kwargs optional).
- `_build_chat_model` extraction: refactor inside `llm_config.py`; will verify behavior parity with current `get_llm` in the regression suite.

## Spec Coverage Map

| Requirement | Where |
|---|---|
| 3 new settings | "Components & Interfaces" → "New settings" |
| Dropdown source | "Components & Interfaces" → ~16 vision models |
| Test endpoint | "Components & Interfaces" → endpoint section |
| Backward compat | "Components & Interfaces" → VisionDescriber + research_service backward compat note |
| Test Connection button | "Components & Interfaces" → WebUI button section |
| Error handling | "Error Handling & Boundaries" table |
| Testing | "Testing Strategy" section |
| Caller pre-verification | "Caller Pre-Verification" section |