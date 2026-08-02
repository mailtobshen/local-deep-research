# PAUSED (2026-08-02): ImageEnhancer is no longer invoked by the
# citation-anchored image pipeline (enhance_report_with_images). Image
# placement is now deterministic — driven by the citation number's
# section — so the LLM position-guesser is bypassed. The class and its
# imports are retained pending confirmation that the new pipeline is
# stable; removal is a separate change. Do not add new callers.
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
from .relevance import _split_sections
from .vision import VisionDescriber

_PROMPT = """You are editing a research report to add real images.

You are seeing ONE section of a larger report at a time (the report is
split into sections for prompt-size reasons). Place any images you
select immediately after this section's heading line (the first line
of the markdown below); do not worry about other sections — a separate
call handles each one. Every image URL you place here will appear in
the final stitched report, so pick the best fit for THIS section only.

STRICT RULES:
- You may ONLY use image URLs from the "Available images" list below.
  The list below is already pre-filtered to images whose source page
  shares a registered domain (eTLD+1, e.g. a1.ctrip.com/... and
  a2.ctrip.com/... both resolve to ctrip.com) with at least one URL
  this section cites. If the list is empty, that means this section
  has no qualifying image — NOT a bug. Insert nothing in that case.
- You MUST NOT invent, modify, or guess any image URL.
- Do NOT change any factual text, numbers, or citations in the report.
- STRICT SAME-SOURCE RULE: For each image, both the image's alt text AND
  its source URL must be topically related to the section you place it in.
  "Same source" means the page the image was crawled from (the value in
  the "source_url" column) is about the same subject as the section AND
  shares the same registered domain as one of the URLs the section
  actually cites. The image list has ALREADY been filtered to satisfy
  this rule — every image you see here is, by construction, from a
  domain cited in this section. If the list looks empty, that means no
  image satisfied the rule for this section: insert nothing (LEAVE THE
  SECTION IMAGE-FREE). Never invent a different source page and never
  reach for an image from a different domain.
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


# Prompt-leak pollution markers. The STRICT RULES paragraph lives in
# _PROMPT (enhancer._PROMPT). When the LLM regurgitates it back, the
# response contains these strings verbatim. Each marker is a short,
# distinctive phrase that's unlikely to appear in genuine report
# content.
_PROMPT_LEAK_MARKERS: tuple[str, ...] = (
    "Since the Available images list is completely empty",
    "According to the STRICT RULES",
    "STRICT SAME-SOURCE RULE",
    "STRICT RULES",
    "Insert nothing in that case.",
    "no qualifying image — NOT a bug",
)


def _response_is_polluted(response: str, chunk: str) -> bool:
    """True when the LLM response echoes prompt text rather than editing
    the section.

    Two pollution shapes are detected:
    1. Verbatim STRICT RULES / Available-images list fragments that
       only live inside the prompt. Their presence is conclusive —
       the response is contaminated, no need to look further.
    2. Net-added prose that's significantly larger than the section
       and doesn't contain a meaningful fraction of the section's
       original text. This catches the "the LLM wrote an essay
       explaining why it didn't insert anything" failure mode that
       looks like a legitimate response but pollutes the report.
    """
    for marker in _PROMPT_LEAK_MARKERS:
        if marker in response:
            return True
    chunk_stripped = chunk.strip()
    if not chunk_stripped:
        return False
    # If the response grew a lot AND lost most of the original chunk,
    # treat it as pollution. A clean edit only adds image markdown
    # lines; it never rewrites the body.
    if len(response) > len(chunk_stripped) * 2 + 400:
        # Heuristic: a single image insertion adds at most ~120 chars
        # (`![alt](https://real/a.jpg)\n`). 2× + 400 accommodates the
        # case where several images were added to a tiny section.
        # Anything beyond that is prose, not an edit.
        overlap = sum(
            1 for line in chunk_stripped.splitlines() if line.strip() and line.strip() in response
        )
        original_lines = sum(
            1 for line in chunk_stripped.splitlines() if line.strip()
        )
        if original_lines > 0 and overlap / original_lines < 0.5:
            return True
    return False


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
        """Single-shot LLM enhancement. On failure returns the chunk unchanged.

        Guard against prompt-leak pollution: when the LLM sees an empty
        image list it sometimes echoes the prompt's STRICT RULES text
        back into its output ("Since the Available images list is
        completely empty..."). The output then contains verbatim
        instructions that don't belong in the report. We detect two
        concrete pollution patterns and reject the response in favor of
        the untouched chunk.
        """
        prompt = _PROMPT.format(
            image_list=_format_list(candidates), markdown=markdown_chunk
        )
        enhanced = self._call_llm_with_trace(prompt)
        if not enhanced:
            return markdown_chunk
        if _response_is_polluted(enhanced, markdown_chunk):
            logger.info(
                "[IMG-TRACE] ENHANCE_REJECTED reason=pollution "
                f"chunk_len={len(markdown_chunk)} "
                f"response_len={len(enhanced)}"
            )
            return markdown_chunk
        return enhanced

    def enhance(
        self,
        markdown: str,
        bank: ImageBank,
        per_section_candidates: dict[int, list[ExtractedImage]] | None = None,
    ) -> str:
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
        # If per_section_candidates was provided, still partition (one
        # section, idx=0) so the prompt sees the section's filtered pool.
        if len(sections) == 1:
            if per_section_candidates is None:
                return self._run_enhance(markdown, candidates)
            section_candidates = per_section_candidates.get(0, [])
            if not section_candidates:
                logger.info(
                    "[IMG-TRACE] SECTION_SKIP idx=0 reason=empty_pool "
                    "heading='' candidates_in_section=0"
                )
                return markdown
            return self._run_enhance(markdown, section_candidates)
        enhanced_parts: list[str] = []
        for idx, (heading, body) in enumerate(sections):
            chunk = (
                f"{heading}\n\n{body}".strip() if heading else body.strip()
            )
            if not chunk:
                continue
            if per_section_candidates is None:
                section_candidates = candidates  # legacy: full pool
            else:
                section_candidates = per_section_candidates.get(idx, [])
            # Skip the LLM call when the per-section pool is empty —
            # the prompt would be a guaranteed no-op and ~2 s per
            # section is wasted. The section markdown passes through
            # unchanged; a SECTION_SKIP line is emitted so an operator
            # can see the skip in IMG-TRACE.
            if not section_candidates:
                logger.info(
                    f"[IMG-TRACE] SECTION_SKIP idx={idx} "
                    f"reason=empty_pool heading={heading[:80]!r} "
                    f"candidates_in_section=0"
                )
                enhanced_parts.append(chunk)
                continue
            enhanced_chunk = self._run_enhance(chunk, section_candidates)
            enhanced_parts.append(enhanced_chunk)
            logger.info(
                f"[IMG-TRACE] SECTION_ENHANCE idx={idx} "
                f"heading={heading[:80]!r} len_in={len(chunk)} "
                f"len_out={len(enhanced_chunk)} "
                f"candidates_in_section={len(section_candidates)}"
            )
        return "\n\n".join(enhanced_parts)