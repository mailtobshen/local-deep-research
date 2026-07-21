"""Post-processing: insert real images into a report via one LLM call."""
from __future__ import annotations

import logging
from typing import List

from .bank import ImageBank
from .extractor import ExtractedImage
from .vision import VisionDescriber

logger = logging.getLogger(__name__)

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
        if not self.vision.enabled:
            return
        for img in bank.candidates_without_alt(limit=20):
            alt = self.vision.describe(img.url)
            if alt:
                bank.set_alt(img.url, alt)

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
