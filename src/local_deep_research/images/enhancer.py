"""Post-processing: insert real images into a report (single-shot)."""
from __future__ import annotations

from typing import List

import httpx
from loguru import logger
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ..security.safe_requests import safe_get
from .bank import ImageBank
from .extractor import ExtractedImage
from .vision import VisionDescriber

_PROMPT = """You are editing a research report to add real images.

STRICT RULES:
- You may ONLY use image URLs from the "Available images" list below.
- You MUST NOT invent, modify, or guess any image URL.
- Do NOT change any factual text, numbers, or citations in the report.
- STRICT SAME-SOURCE RULE: For each image, both the image's alt text AND
  its source URL must be topically related to the section you place it in.
  "Same source" means the page the image was crawled from is about the
  same subject as the section. If a section has no image whose source
  page matches, LEAVE THAT SECTION IMAGE-FREE — do not borrow an
  image from a different source.
- Each image URL may appear at most ONCE in the output.
- Insert images using markdown: ![alt](url), placed immediately after the
  section's heading line.
- If no available image fits a section, insert nothing there — never force an image.

Available images (url | alt | source_url):
{image_list}

Report to enhance:
---
{markdown}
---

Return ONLY the enhanced report markdown, nothing else."""


# Module-level defaults used when callers (e.g. unit tests) don't supply
# their own. The runtime always passes values from `report.image_vision_*`
# settings via `ImageEnhancer(min_alt_count=..., cap=...)`.
DEFAULT_VISION_MIN_ALT_TRIGGER = 3
DEFAULT_VISION_CAP = 10


def _format_list(images: List[ExtractedImage]) -> str:
    return "\n".join(
        f"- {i.url} | {i.alt} | {i.source_url or '(unknown)'}"
        for i in images
    )


def _extract_base_url(llm) -> str:
    """Best-effort base URL from a LangChain chat model.

    The exact attribute differs by class: ``ChatOpenAI`` exposes
    ``openai_api_base``; ``ChatOllama`` exposes ``base_url``; bare
    wrappers expose ``client.base_url``. Returns "" when nothing
    recognisable is found.
    """
    for attr in ("openai_api_base", "base_url"):
        v = getattr(llm, attr, None)
        if isinstance(v, str) and v:
            return v
    inner = getattr(llm, "client", None) or getattr(llm, "_client", None)
    if inner is not None:
        v = getattr(inner, "base_url", None)
        if isinstance(v, str) and v:
            return v
    return ""


def _extract_model(llm) -> str:
    for attr in ("model_name", "model"):
        v = getattr(llm, attr, None)
        if isinstance(v, str) and v:
            return v
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


class ImageEnhancer:
    def __init__(
        self,
        llm,
        vision: VisionDescriber,
        min_alt_count: int = DEFAULT_VISION_MIN_ALT_TRIGGER,
        cap: int = DEFAULT_VISION_CAP,
        allow_vision_fill: bool = False,
    ) -> None:
        self.llm = llm
        self.vision = vision
        self.min_alt_count = min_alt_count
        self.cap = cap
        # When False (the strict default), the report-path caller has
        # already filtered candidates through the context-entity gate
        # and running the Vision describer is unwanted drift. Tests
        # that exercise the legacy Vision-fill path must opt in
        # explicitly.
        self.allow_vision_fill = allow_vision_fill

    def _vision_fill(self, bank: ImageBank) -> None:
        total_without_alt = len(bank.candidates_without_alt(limit=10**9))
        to_describe = bank.candidates_without_alt(limit=self.cap)
        dropped = max(0, total_without_alt - len(to_describe))
        logger.info(
            f"[IMG-TRACE] VISION begin enabled={self.vision.enabled} "
            f"to_describe={len(to_describe)} cap={self.cap} dropped={dropped}"
        )
        if not self.vision.enabled:
            logger.info(
                "[IMG-TRACE] VISION end attempted=0 filled=0 (disabled)"
            )
            return
        attempted = 0
        filled = 0
        for img in to_describe:
            attempted += 1
            alt = self.vision.describe(img.url)
            if alt:
                bank.set_alt(img.url, alt)
                filled += 1
                logger.info(
                    f'[IMG-TRACE] VISION_FILL url={img.url} alt=OK text="{alt}"'
                )
            else:
                logger.info(
                    f"[IMG-TRACE] VISION_FILL url={img.url} alt=FAIL"
                )
        logger.info(
            f"[IMG-TRACE] VISION end attempted={attempted} filled={filled}"
        )

    def _call_llm_with_trace(self, prompt: str):
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
        return self._run_enhance(markdown, candidates)