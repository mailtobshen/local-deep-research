"""Post-processing: insert real images into a report (single-shot)."""
from __future__ import annotations

from typing import List

from loguru import logger

from .bank import ImageBank
from .extractor import ExtractedImage
from .vision import VisionDescriber

_PROMPT = """You are editing a research report to add real images.

STRICT RULES:
- You may ONLY use image URLs from the "Available images" list below.
- You MUST NOT invent, modify, or guess any image URL.
- Do NOT change any factual text, numbers, or citations in the report.
- PAIR BY ALT TEXT: place an image ONLY in a section whose subject matches
  the image's alt text. The alt text is the canonical name of what is
  pictured; the section heading + first sentence describes the same thing.
- NEVER substitute a near-match image for a missing exact match. If no
  available image has an alt that names the subject of a section, leave
  that section image-free.
- Each image URL may appear at most ONCE in the output.
- Insert images using markdown: ![alt](url), placed immediately after the
  section's heading line.
- If no available image fits a section, insert nothing there — never force an image.

Available images (url | alt | source title):
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
    return "\n".join(f"- {i.url} | {i.alt} | {i.source_title}" for i in images)


class ImageEnhancer:
    def __init__(
        self,
        llm,
        vision: VisionDescriber,
        min_alt_count: int = DEFAULT_VISION_MIN_ALT_TRIGGER,
        cap: int = DEFAULT_VISION_CAP,
    ) -> None:
        self.llm = llm
        self.vision = vision
        self.min_alt_count = min_alt_count
        self.cap = cap

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

    def _run_enhance(
        self, markdown_chunk: str, candidates: List[ExtractedImage]
    ) -> str:
        """Single-shot LLM enhancement. On failure returns the chunk unchanged."""
        try:
            prompt = _PROMPT.format(
                image_list=_format_list(candidates), markdown=markdown_chunk
            )
            resp = self.llm.invoke(prompt)
            enhanced = str(getattr(resp, "content", "")).strip()
            return enhanced if enhanced else markdown_chunk
        except Exception:
            logger.exception(
                "Image enhancement failed; returning original chunk"
            )
            return markdown_chunk

    def enhance(self, markdown: str, bank: ImageBank) -> str:
        candidates = bank.candidates_with_alt()
        # Vision fill when the bank is already rich would be wasted cost —
        # only run it when we genuinely lack alt coverage AND a vision model
        # is configured.
        if (
            len(candidates) <= self.min_alt_count
            and self.vision.enabled
        ):
            self._vision_fill(bank)
            candidates = bank.candidates_with_alt()
        if not candidates:
            return markdown
        return self._run_enhance(markdown, candidates)