"""Post-processing: insert real images into a report via one LLM call."""
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
- Insert images where topically relevant using markdown: ![alt](url)
- If no available image fits a section, insert nothing there — never force an image.

Available images (url | alt | source title):
{image_list}

Report to enhance:
---
{markdown}
---

Return ONLY the enhanced report markdown, nothing else."""


def _format_list(images: List[ExtractedImage]) -> str:
    return "\n".join(f"- {i.url} | {i.alt} | {i.source_title}" for i in images)


class ImageEnhancer:
    def __init__(self, llm, vision: VisionDescriber) -> None:
        self.llm = llm
        self.vision = vision

    def _vision_fill(self, bank: ImageBank) -> None:
        total_without_alt = len(bank.candidates_without_alt(limit=10**9))
        to_describe = bank.candidates_without_alt(limit=20)
        dropped = max(0, total_without_alt - len(to_describe))
        logger.info(
            f"[IMG-TRACE] VISION begin enabled={self.vision.enabled} "
            f"to_describe={len(to_describe)} cap=20 dropped={dropped}"
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

    def enhance(self, markdown: str, bank: ImageBank) -> str:
        candidates = bank.candidates_with_alt()
        if not candidates:
            self._vision_fill(bank)
            candidates = bank.candidates_with_alt()
            if not candidates:
                return markdown
        try:
            prompt = _PROMPT.format(
                image_list=_format_list(candidates), markdown=markdown
            )
            resp = self.llm.invoke(prompt)
            enhanced = str(getattr(resp, "content", "")).strip()
            if not enhanced:
                return markdown
            return enhanced
        except Exception:
            logger.exception(
                "Image enhancement failed; returning original markdown"
            )
            return markdown
