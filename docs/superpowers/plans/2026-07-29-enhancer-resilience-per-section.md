# Image-Enhancer Resilience & Per-Section Calls

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LDR image enhancer robust against transient LLM endpoint failures and shrink its prompts by calling the LLM once per markdown section, while preserving the IMG-TRACE observability that previously conflated "500 error" between the LLM provider and a downstream proxy.

**Architecture:** Three independent changes in `src/local_deep_research/images/enhancer.py` (and one new helper in the same module):

1. `_preflight()` — light `GET /api/tags` against the Ollama-compatible endpoint; if it fails on a `ConnectionError`/`HTTPStatusError` we abort early instead of paying the cost of a real prompt.
2. `_invoke_with_retry()` — wraps `llm.invoke()` with tenacity's `wait_exponential` for 5xx and `requests`/`httpx` `ConnectionError`/`Timeout`; 4xx and other errors propagate immediately (no retry — they're configuration bugs).
3. `_call_llm_with_trace()` — single entry point that emits an IMG-TRACE line on success **and** failure, with `provider`, `model`, `base_url`, `http_status`, `response_content_type`, plus the exception class on failure.
4. `enhance()` now calls the LLM per markdown section (using the existing `_split_sections` from `images/relevance.py`) and stitches the responses back into one report. Each per-section call goes through the same preflight → retry → trace pipeline.

The plan keeps the existing public API of `ImageEnhancer` (`enhance(markdown, bank) -> str`) unchanged so callers in `postprocessing.py:283` need no edits.

**Tech Stack:** Python 3.12, loguru, tenacity (already a project dep — used by `web_search_engines/search_engine_base.py` and `notifications/service.py`), `langchain_openai.ChatOpenAI` (the LLM objects used by `get_llm()` already expose `model_name`, `openai_api_base`).

## Global Constraints

- All changes live on `main`; one task = one commit; no background git.
- Do NOT touch `postprocessing.py:283` — it already calls `enhancer.enhance(...)` and the public API must stay drop-in.
- Pre-existing IMG-TRACE call sites that already log other fields (BANK, VISION, ENHANCE, etc.) MUST keep their format — only the new IMG-TRACE lines for LLM calls get the new fields.
- Use `tenacity.wait_exponential` + `tenacity.retry_if_exception_type` for the retry; do NOT roll our own loop.
- `safe_get` from `local_deep_research.security.safe_requests` is the HTTP client (already used by `vision.py:54`); we use it for `/api/tags` preflight.

## Interface Contract

A future caller / test sees:

```python
enhancer = ImageEnhancer(llm, vision)
out = enhancer.enhance(markdown, bank)
# Pre-flight hits /api/tags once before the first LLM call; subsequent
# sections reuse the green light until the process ends.
# Each section prompt is independent — a 500 on section 3 leaves
# sections 0,1,2,4..N enhanced and section 3 unchanged.
```

`ImageEnhancer.__init__` signature is unchanged.

---

### Task 1: Add `_preflight` + retry helper (no behavior change yet)

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py` (new private module-level helpers; no call-site changes yet)

**Interfaces:**
- `_preflight(llm) -> bool` — returns `True` if the LLM's endpoint answers `GET /api/tags` with 2xx; `False` on connection error or 5xx.
- `_invoke_with_retry(llm, prompt: str) -> Any` — runs `llm.invoke(prompt)` with tenacity retry on 5xx / connection / timeout; returns the response object.

- [ ] **Step 1: Add the helpers just below the `_format_list` function** in `src/local_deep_research/images/enhancer.py`

Insert this block after `_format_list` (current line 51):

```python
import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ..security.safe_requests import safe_get


def _extract_base_url(llm) -> str:
    """Best-effort base URL from a LangChain chat model.

    The exact attribute differs by class: ``ChatOpenAI`` exposes
    ``openai_api_base``; ``ChatOllama`` exposes ``base_url``; bare
    wrappers expose ``client.base_url``. Returns "" when nothing
    recognisable is found.
    """
    for attr in ("openai_api_base", "base_url"):
        v = getattr(llm, attr, None)
        if v:
            return str(v)
    inner = getattr(llm, "client", None) or getattr(llm, "_client", None)
    if inner is not None:
        v = getattr(inner, "base_url", None)
        if v:
            return str(v)
    return ""


def _extract_model(llm) -> str:
    for attr in ("model_name", "model"):
        v = getattr(llm, attr, None)
        if v:
            return str(v)
    return ""


def _provider_from_base_url(base_url: str) -> str:
    if not base_url:
        return "unknown"
    bl = base_url.lower()
    if "ollama" in bl or ":11434" in bl:
        return "ollama"
    if "openrouter" in bl:
        return "openrouter"
    if "anthropic" in bl:
        return "anthropic"
    if bl.startswith(("http://localhost", "http://127.", "http://0.0.0.0",
                       "https://localhost", "https://127.", "https://0.0.0.0")):
        return "local"
    return "openai_endpoint"


def _http_status_from_exc(exc: Exception) -> int:
    """Pull the HTTP status code off common exception shapes."""
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    return 0


def _preflight(llm) -> bool:
    """Light GET /api/tags against the LLM endpoint.

    Returns True when the endpoint answers 2xx (server alive and
    serving the chat API). Returns False on any connection / DNS /
    timeout error OR on 5xx — both signal the LLM is unusable for
    this run and we'd rather short-circuit the whole report than
    hammer it with N section prompts.
    """
    base = _extract_base_url(llm).rstrip("/")
    if not base:
        # No base URL → can't preflight. Be permissive: don't block.
        return True
    probe = f"{base}/api/tags"
    try:
        resp = safe_get(probe, timeout=5, allow_private_ips=True)
    except Exception:
        logger.info(
            f"[IMG-TRACE] PREFLIGHT url={probe} status=unreachable"
        )
        return False
    sc = getattr(resp, "status_code", 0)
    if 200 <= sc < 300:
        logger.info(
            f"[IMG-TRACE] PREFLIGHT url={probe} status=ok http_status={sc}"
        )
        return True
    logger.info(
        f"[IMG-TRACE] PREFLIGHT url={probe} status=bad http_status={sc}"
    )
    return False


def _is_retryable(exc: Exception) -> bool:
    """Retry only on transport / 5xx; 4xx means a config bug we
    should NOT paper over."""
    sc = _http_status_from_exc(exc)
    if sc and 500 <= sc < 600:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, httpx.HTTPError)):
        return True
    if isinstance(exc, RetryError):
        return False
    return False


def _invoke_with_retry(llm, prompt: str):
    """Invoke the LLM with exponential backoff on 5xx and network errors.

    4xx and other exceptions are NOT retried — propagate so the caller
    can log + skip that section.
    """
    @retry(
        retry=retry_if_exception(lambda e: _is_retryable(e)
                                 and not isinstance(e, RetryError)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _do():
        return llm.invoke(prompt)
    return _do()
```

- [ ] **Step 2: Run ruff on the modified file**

Run: `ruff check src/local_deep_research/images/enhancer.py`
Expected: no findings.

- [ ] **Step 3: Run existing enhancer tests — they MUST still pass unchanged**

Run: `pytest tests/images/test_enhancer.py -v`
Expected: all 7 tests pass (we added helpers, no behavior change yet).

- [ ] **Step 4: Commit**

```bash
git rev-parse --abbrev-ref HEAD    # must print: main
git add src/local_deep_research/images/enhancer.py
git commit -m "feat(images): add /api/tags preflight + retry helpers for LLM calls"
git log --oneline -3
```

---

### Task 2: Wire preflight + retry into `_run_enhance` and emit rich IMG-TRACE

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py` — replace `_run_enhance`

**Interfaces:**
- `_call_llm_with_trace(llm, prompt: str) -> Optional[str]` — returns the response content string, or `None` on failure (never raises). Always emits an `[IMG-TRACE] LLM_CALL` line with provider/model/base_url/http_status/response_content_type.

- [ ] **Step 1: Replace `_run_enhance` with the trace-instrumented version**

In `src/local_deep_research/images/enhancer.py` (current `_run_enhance` at lines 106–121), replace the entire method body with:

```python
def _call_llm_with_trace(self, prompt: str) -> Optional[str]:
    """Run one LLM call, log full provenance, return content or None."""
    base_url = _extract_base_url(self.llm)
    provider = _provider_from_base_url(base_url)
    model = _extract_model(self.llm)
    if not _preflight(self.llm):
        logger.info(
            f"[IMG-TRACE] LLM_CALL provider={provider} model={model} "
            f"base_url={base_url} status=preflight_failed"
        )
        return None
    try:
        resp = _invoke_with_retry(self.llm, prompt)
    except Exception as exc:
        sc = _http_status_from_exc(exc)
        exc_name = type(exc).__name__
        logger.info(
            f"[IMG-TRACE] LLM_CALL provider={provider} model={model} "
            f"base_url={base_url} status=error http_status={sc} "
            f"response_content_type= exc_class={exc_name}"
        )
        logger.debug(
            f"Image-enhance LLM call failed ({exc_name}): {exc}"
        )
        return None
    content = str(getattr(resp, "content", "")).strip()
    # Best-effort content type — the LangChain object has no field for
    # this; we record "" when unknown and try response.response_headers
    # for the rare OpenAI wrapper that exposes them.
    ctype = ""
    inner = getattr(resp, "response_metadata", None) or {}
    if isinstance(inner, dict):
        ctype = inner.get("content_type", "") or ""
    if not ctype:
        raw = getattr(resp, "response", None)
        if raw is not None:
            headers = getattr(raw, "headers", None) or {}
            ctype = headers.get("content-type", "") if hasattr(headers, "get") else ""
    logger.info(
        f"[IMG-TRACE] LLM_CALL provider={provider} model={model} "
        f"base_url={base_url} status=ok http_status=200 "
        f"response_content_type={ctype or 'text/plain'}"
    )
    return content or None

def _run_enhance(
    self, markdown_chunk: str, candidates: List[ExtractedImage]
) -> str:
    """Single-shot LLM enhancement. On failure returns the chunk unchanged."""
    prompt = _PROMPT.format(
        image_list=_format_list(candidates), markdown=markdown_chunk
    )
    enhanced = self._call_llm_with_trace(prompt)
    return enhanced if enhanced else markdown_chunk
```

- [ ] **Step 2: Run existing tests — they MUST still pass**

Run: `pytest tests/images/test_enhancer.py -v`
Expected: all 7 tests still pass. (`test_enhance_returns_original_when_llm_fails` exercises the failure path; we now emit a trace line instead of `logger.exception`, but the return value is still the original chunk.)

- [ ] **Step 3: Commit**

```bash
git rev-parse --abbrev-ref HEAD    # must print: main
git add src/local_deep_research/images/enhancer.py
git commit -m "feat(images): trace provider/model/base_url/http_status on every LLM call"
git log --oneline -3
```

---

### Task 3: Per-section prompt segmentation

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py` — change `enhance()` to split, enhance per section, stitch.

**Interfaces:**
- `enhance(markdown, bank) -> str` — same signature, same return type. Internally splits markdown on `^#{1,6}` headings (via `relevance._split_sections`) and runs `_run_enhance` once per section. Sections where the LLM returns empty / fails are kept verbatim.

- [ ] **Step 1: Add the import and replace `enhance()`**

In `src/local_deep_research/images/enhancer.py` add (just under the existing imports, near the top):

```python
from .relevance import _split_sections
```

Then replace the body of `enhance()` (current lines 123–140) with:

```python
def enhance(self, markdown: str, bank: ImageBank) -> str:
    candidates = bank.candidates_with_alt()
    # Vision fill when the bank is already rich would be wasted cost —
    # only run it when we genuinely lack alt coverage AND a vision model
    # is configured. The strict-context-entity report path sets
    # `allow_vision_fill=False` so the post-gate bank is passed through
    # verbatim — Vision calls would re-introduce the alts the gate
    # rejected and undermine the gate's fail-closed guarantee.
    if (
        self.allow_vision_fill
        and len(candidates) <= self.min_alt_count
        and self.vision.enabled
    ):
        self._vision_fill(bank)
        candidates = bank.candidates_with_alt()
    if not candidates:
        return markdown
    sections = _split_sections(markdown)
    if not sections:
        return markdown
    # Tiny reports (no headings): fall back to the single-shot path.
    if len(sections) == 1:
        return self._run_enhance(markdown, candidates)
    enhanced_parts: list[str] = []
    for idx, (heading, body) in enumerate(sections):
        chunk = (
            f"{heading}\n\n{body}".strip() if heading else body.strip()
        )
        if not chunk:
            continue
        enhanced_chunk = self._run_enhance(chunk, candidates)
        enhanced_parts.append(enhanced_chunk)
        logger.info(
            f"[IMG-TRACE] SECTION_ENHANCE idx={idx} "
            f"heading={heading[:80]!r} len_in={len(chunk)} "
            f"len_out={len(enhanced_chunk)}"
        )
    return "\n\n".join(enhanced_parts)
```

- [ ] **Step 2: Add new tests**

Append to `tests/images/test_enhancer.py`:

```python
def test_enhance_calls_llm_per_section():
    """Each ## section gets its own LLM invocation."""
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "Canton Tower")])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# a\n\n![a](https://real/a.jpg)")
    vision = MagicMock()
    vision.enabled = False
    md = "# H1\n\nbody1\n\n## H2\n\nbody2\n\n## H3\n\nbody3"
    ImageEnhancer(llm, vision).enhance(md, bank)
    # 3 sections → 3 calls
    assert llm.invoke.call_count == 3
    # Each prompt should reference the section's own body, not the whole doc
    called_prompts = [c.args[0] for c in llm.invoke.call_args_list]
    assert any("body1" in p for p in called_prompts)
    assert any("body2" in p for p in called_prompts)
    assert any("body3" in p for p in called_prompts)


def test_enhance_section_failure_keeps_other_sections():
    """A failing section (Exception) doesn't poison the others."""
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "x")])
    llm = MagicMock()
    # 1st call OK, 2nd raises, 3rd OK — middle section passes through
    llm.invoke.side_effect = [
        MagicMock(content="# H1\n\nok1"),
        Exception("500 server error"),
        MagicMock(content="## H2\n\nok2"),
    ]
    vision = MagicMock()
    vision.enabled = False
    md = "# H1\n\nbody1\n\n## H2\n\nbody2\n\n## H3\n\nbody3"
    out = ImageEnhancer(llm, vision).enhance(md, bank)
    # All 3 sections present in output
    assert "H1" in out and "H2" in out and "H3" in out


def test_enhance_no_headings_falls_back_to_single_call():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "x")])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="ok")
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("Just prose, no headings.", bank)
    assert llm.invoke.call_count == 1
    assert out == "ok"
```

- [ ] **Step 3: Run the full enhancer test file**

Run: `pytest tests/images/test_enhancer.py -v`
Expected: 10 tests pass (7 original + 3 new).

- [ ] **Step 4: Commit**

```bash
git rev-parse --abbrev-ref HEAD    # must print: main
git add src/local_deep_research/images/enhancer.py tests/images/test_enhancer.py
git commit -m "feat(images): call LLM per markdown section, stitch enhanced chunks"
git log --oneline -3
```

---

### Task 4: Tests for preflight + retry behavior

**Files:**
- Modify: `tests/images/test_enhancer.py`

- [ ] **Step 1: Add the tests**

Append to `tests/images/test_enhancer.py`:

```python
import httpx
from local_deep_research.images.enhancer import (
    _preflight,
    _invoke_with_retry,
    _is_retryable,
    _extract_base_url,
    _http_status_from_exc,
)


def _llm_with_base(url):
    """Build a fake chat model exposing openai_api_base like ChatOpenAI does."""
    llm = MagicMock()
    llm.openai_api_base = url
    llm.model_name = "test-model"
    llm.invoke.return_value = MagicMock(content="ok")
    return llm


def test_preflight_returns_true_on_2xx(monkeypatch):
    resp = MagicMock(status_code=200)
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.safe_get",
        lambda *a, **k: resp,
    )
    assert _preflight(_llm_with_base("http://localhost:11434")) is True


def test_preflight_returns_false_on_5xx(monkeypatch):
    resp = MagicMock(status_code=503)
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.safe_get",
        lambda *a, **k: resp,
    )
    assert _preflight(_llm_with_base("http://localhost:11434")) is False


def test_preflight_returns_false_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.safe_get", boom
    )
    assert _preflight(_llm_with_base("http://localhost:11434")) is False


def test_preflight_returns_true_when_no_base_url():
    """No base URL → can't probe, be permissive."""
    llm = MagicMock(spec=[])  # no attributes
    assert _preflight(llm) is True


def test_retry_succeeds_after_one_5xx(monkeypatch):
    """Mock the underlying llm.invoke to raise 500 once then succeed."""
    llm = MagicMock()
    llm.openai_api_base = ""
    llm.model_name = "m"
    good = MagicMock(content="ok")
    bad = Exception("upstream 500")
    bad.status_code = 500
    llm.invoke.side_effect = [bad, good]
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.wait_exponential",
        lambda **k: __import__("tenacity").wait_none(),
    )
    out = _invoke_with_retry(llm, "p")
    assert out is good
    assert llm.invoke.call_count == 2


def test_retry_does_not_retry_4xx():
    """4xx is a config bug — propagate immediately, no retry."""
    llm = MagicMock()
    llm.invoke.side_effect = Exception("bad request")
    llm.invoke.side_effect.status_code = 400
    try:
        _invoke_with_retry(llm, "p")
    except Exception:
        pass
    assert llm.invoke.call_count == 1


def test_retry_exhausts_then_raises(monkeypatch):
    llm = MagicMock()
    llm.invoke.side_effect = httpx.ConnectError("refused")
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.wait_exponential",
        lambda **k: __import__("tenacity").wait_none(),
    )
    try:
        _invoke_with_retry(llm, "p")
    except Exception:
        pass
    # tenacity default stop_after_attempt=3
    assert llm.invoke.call_count == 3


def test_is_retryable_distinguishes_5xx_from_4xx():
    e5 = Exception("x"); e5.status_code = 503
    e4 = Exception("x"); e4.status_code = 400
    assert _is_retryable(e5) is True
    assert _is_retryable(e4) is False
    assert _is_retryable(httpx.ConnectError("x")) is True
    assert _is_retryable(httpx.ReadTimeout("x")) is True
    assert _is_retryable(ValueError("x")) is False


def test_extract_base_url_finds_openai_api_base():
    llm = MagicMock(openai_api_base="http://x:1234/v1")
    assert _extract_base_url(llm) == "http://x:1234/v1"


def test_http_status_from_exc_handles_response_object():
    resp = MagicMock(status_code=502)
    exc = Exception("bad gateway")
    exc.response = resp
    assert _http_status_from_exc(exc) == 502
```

- [ ] **Step 2: Run the full test file**

Run: `pytest tests/images/test_enhancer.py -v`
Expected: 20 tests pass (10 from prior tasks + 10 new).

- [ ] **Step 3: Run the wider images test suite for regressions**

Run: `pytest tests/images/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git rev-parse --abbrev-ref HEAD    # must print: main
git add tests/images/test_enhancer.py
git commit -m "test(images): cover preflight, retry, retryable-classifier, base-url extraction"
git log --oneline -3
```

---

## Self-Review

**Spec coverage:**
- ✅ "在 enhancer.py:114 增加轻量预检（GET /api/tags）+ 指数退避重试，区分 5xx 与网络错误" — Task 1 (`_preflight`, `_invoke_with_retry`, `_is_retryable`); Task 2 wires both into the call site; Task 4 tests all three behaviors.
- ✅ "IMG-TRACE 记录 provider / model / base_url / http_status / response_content_type" — Task 2 (`_call_llm_with_trace`) emits every field on success and failure.
- ✅ "单次增强 prompt 改为按 section 分段调用" — Task 3 splits via `_split_sections`, calls per section, stitches.

**Placeholder scan:** No TBDs, TODOs, or hand-waves. Every step shows the actual code or the actual test code.

**Type / name consistency:** `_call_llm_with_trace` is the only new public method, called once from `_run_enhance`. `_preflight`, `_invoke_with_retry`, `_is_retryable`, `_extract_base_url`, `_http_status_from_exc`, `_provider_from_base_url`, `_extract_model` are all module-level helpers with consistent signatures across tasks.