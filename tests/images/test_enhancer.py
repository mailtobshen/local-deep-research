# tests/images/test_enhancer.py
import httpx
from unittest.mock import MagicMock
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank
from local_deep_research.images.enhancer import (
    ImageEnhancer,
    _extract_base_url,
    _http_status_from_exc,
    _invoke_with_retry,
    _is_retryable,
    _preflight,
)


def _img(url, alt):
    return ExtractedImage(
        url=url, alt=alt, source_url="s", source_title="t", width=None, height=None
    )


def test_enhance_inserts_real_url_from_bank():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "Canton Tower")])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="# Report\n\n![Canton Tower](https://real/a.jpg)\n"
    )
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report\n\ntext", bank)
    assert "https://real/a.jpg" in out
    llm.invoke.assert_called_once()


def test_enhance_returns_original_when_llm_fails():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "x")])
    llm = MagicMock()
    llm.invoke.side_effect = Exception("boom")
    vision = MagicMock()
    vision.enabled = False
    original = "# Report\n\ntext"
    assert ImageEnhancer(llm, vision).enhance(original, bank) == original


def test_enhance_skips_when_bank_empty():
    bank = ImageBank()
    llm = MagicMock()
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report", bank)
    assert out == "# Report"
    llm.invoke.assert_not_called()


def test_vision_triggered_when_with_alt_below_threshold():
    """≤ 3 alts + alt-less images present → vision fills up to cap=10."""
    bank = ImageBank()
    # 2 with-alt (under the default min_alt_count=3 threshold) + 5 without.
    bank.add(
        [_img(f"https://real/{i}.jpg", "alt") for i in range(2)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    # allow_vision_fill=True to exercise the legacy Vision-fill path
    # that the report path now disables.
    ImageEnhancer(llm, vision, allow_vision_fill=True).enhance("# R", bank)
    assert vision.describe.call_count == 5


def test_vision_skipped_when_bank_already_rich():
    """> min_alt_count alts → vision not invoked even if configured."""
    bank = ImageBank()
    # 5 with-alt + 5 without — well above the 3-trigger, vision is overkill.
    bank.add(
        [_img(f"https://real/{i}.jpg", f"alt {i}") for i in range(5)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="![a](https://real/0.jpg)"
    )
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision, allow_vision_fill=True).enhance(
        "# R\n\nbody", bank
    )
    vision.describe.assert_not_called()


def test_vision_caps_at_limit():
    """Even with many alt-less images, vision describes at most cap."""
    cap = 3

    bank = ImageBank()
    bank.add(
        [_img(f"https://real/{i}.jpg", "alt") for i in range(2)]  # trigger
        + [_img(f"https://real/n{i}.jpg", "") for i in range(cap + 5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(
        llm, vision, cap=cap, allow_vision_fill=True
    ).enhance("# R", bank)
    assert vision.describe.call_count == cap


def test_vision_thresholds_are_overridable_per_instance():
    """Custom min_alt_count / cap override module defaults."""
    bank = ImageBank()
    # 4 with-alt + 4 without. With min_alt_count=5 (high), vision runs;
    # with cap=2, only 2 vision calls.
    bank.add(
        [_img(f"https://real/{i}.jpg", f"alt {i}") for i in range(4)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(4)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(
        llm, vision, min_alt_count=5, cap=2, allow_vision_fill=True
    ).enhance("# R", bank)
    assert vision.describe.call_count == 2



def test_enhancer_default_disables_vision_fill():
    """The strict default is `allow_vision_fill=False`: the bank is below
    the min-alt-count threshold but Vision must NOT be invoked."""
    bank = ImageBank()
    # 2 with-alt + 5 without \u2014 triggers the Vision branch when allowed.
    bank.add(
        [_img(f"https://real/{i}.jpg", "alt") for i in range(2)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision).enhance("# R", bank)
    vision.describe.assert_not_called()


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
    # 3 sections \u2192 3 calls
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
    # Echo-back the prompt's section heading into the response so we can
    # tell which section's output is in the joined result. Sections 1 and
    # 3 succeed (echo their heading); section 2 raises (Exception).
    def _echo(prompt):
        m = MagicMock()
        if "body1" in prompt:
            m.content = "# H1\n\nok1"
        elif "body3" in prompt:
            m.content = "## H3\n\nok3"
        else:
            raise Exception("500 server error")
        return m
    llm.invoke.side_effect = _echo
    vision = MagicMock()
    vision.enabled = False
    md = "# H1\n\nbody1\n\n## H2\n\nbody2\n\n## H3\n\nbody3"
    out = ImageEnhancer(llm, vision).enhance(md, bank)
    # All 3 section headings must be in the output, and section 2's
    # verbatim body must survive intact because the LLM call failed.
    assert "ok1" in out and "ok3" in out and "body2" in out


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


# ---------------------------------------------------------------------------
# Task 4: preflight + retry helpers
# ---------------------------------------------------------------------------


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
    import tenacity as _t

    llm = MagicMock()
    llm.openai_api_base = ""
    llm.model_name = "m"
    good = MagicMock(content="ok")
    bad = Exception("upstream 500")
    bad.status_code = 500
    llm.invoke.side_effect = [bad, good]
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.wait_exponential",
        lambda **k: _t.wait_none(),
    )
    out = _invoke_with_retry(llm, "p")
    assert out is good
    assert llm.invoke.call_count == 2


def test_retry_does_not_retry_4xx(monkeypatch):
    """4xx is a config bug — propagate immediately, no retry."""
    import tenacity as _t

    llm = MagicMock()
    llm.invoke.side_effect = Exception("bad request")
    llm.invoke.side_effect.status_code = 400
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.wait_exponential",
        lambda **k: _t.wait_none(),
    )
    raised = False
    try:
        _invoke_with_retry(llm, "p")
    except Exception:
        raised = True
    assert raised
    assert llm.invoke.call_count == 1


def test_retry_exhausts_then_raises(monkeypatch):
    import tenacity as _t

    llm = MagicMock()
    llm.invoke.side_effect = httpx.ConnectError("refused")
    monkeypatch.setattr(
        "local_deep_research.images.enhancer.wait_exponential",
        lambda **k: _t.wait_none(),
    )
    raised = False
    try:
        _invoke_with_retry(llm, "p")
    except Exception:
        raised = True
    assert raised
    # tenacity default stop_after_attempt=3
    assert llm.invoke.call_count == 3


def test_is_retryable_distinguishes_5xx_from_4xx():
    e5 = Exception("x")
    e5.status_code = 503
    e4 = Exception("x")
    e4.status_code = 400
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
