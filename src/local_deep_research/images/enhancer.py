"""Post-processing: insert real images into a report, optionally per-section."""
from __future__ import annotations

import re
from typing import List, Tuple

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


# Latin word + per-CJK-character tokens. CJK has no spaces, so per-char is the
# cheapest correct tokenization (no jieba dependency).
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")
# H1-H3 heading splitter. H4+ stays in the previous section's body.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)

# When the candidate count is below this, skip per-section filtering — the
# full prompt still fits comfortably and the extra section splitting is
# pure overhead. Tuned for the typical research run (~30-200 candidates).
_FILTER_THRESHOLD = 30
# Sections shorter than this skip filtering — too little text to distinguish
# relevant candidates from noise. Heading line alone (e.g. "## 广州塔") is
# ~10 chars and is meaningful enough to filter on, but a heading with no
# body adds no signal beyond what the heading already provides, so the
# full section needs at least one sentence to anchor the match.
_MIN_SECTION_CHARS = 15


def _format_list(images: List[ExtractedImage]) -> str:
    return "\n".join(f"- {i.url} | {i.alt} | {i.source_title}" for i in images)


def _split_sections(markdown: str) -> List[Tuple[str, str]]:
    """Split a markdown report on H1-H3 headings.

    Returns [(heading_line, body_text), ...]. The pre-first-heading preamble
    is dropped — research reports without headings are degenerate for image
    pairing and the caller falls back to the single-shot path anyway.
    """
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return []
    sections: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        start_next = (
            matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        )
        # heading_line covers the full match span (heading text + trailing
        # newline) so callers can reconstruct the original markdown by
        # concatenating heading_line + body.
        sections.append((m.group(0), markdown[m.end():start_next]))
    return sections


def _tokens(text: str) -> set[str]:
    """Latin words + individual CJK characters, lowercased.

    Per-CJK-character is intentional: a Chinese phrase like "圣心大教堂"
    tokenizes to {"圣", "心", "大", "教", "堂"} and matches section text
    that contains any of those characters. Good enough for the
    "drop obviously-unrelated candidates" job.
    """
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _section_alt_overlap(section_text: str, alts: List[str]) -> set[int]:
    """Indices of `alts` sharing >= 1 token with section_text."""
    sec_tokens = _tokens(section_text)
    if not sec_tokens:
        return set()
    matched: set[int] = set()
    for i, alt in enumerate(alts):
        if alt and sec_tokens & _tokens(alt):
            matched.add(i)
    return matched


def _filter_for_section(
    section_text: str, candidates: List[ExtractedImage]
) -> List[ExtractedImage]:
    """Return candidates relevant to this section.

    Falls back to the full candidate list when the section is too short to
    filter reliably, or when no candidate shares any token with the section
    (better to let the LLM see everything than to give it an empty menu).
    """
    if len(section_text.strip()) < _MIN_SECTION_CHARS:
        return candidates
    alts = [c.alt for c in candidates]
    matched_idx = _section_alt_overlap(section_text, alts)
    if not matched_idx:
        return candidates
    return [c for i, c in enumerate(candidates) if i in matched_idx]


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
        if not candidates:
            self._vision_fill(bank)
            candidates = bank.candidates_with_alt()
            if not candidates:
                return markdown

        # Fast path: small candidate set → single-shot, no section overhead.
        if len(candidates) < _FILTER_THRESHOLD:
            logger.info(
                f"[IMG-TRACE] SECTION_FILTER mode=off "
                f"candidates={len(candidates)} reason=small"
            )
            return self._run_enhance(markdown, candidates)

        sections = _split_sections(markdown)
        if len(sections) < 2:
            logger.info(
                f"[IMG-TRACE] SECTION_FILTER mode=off "
                f"candidates={len(candidates)} reason=no_sections"
            )
            return self._run_enhance(markdown, candidates)

        logger.info(
            f"[IMG-TRACE] SECTION_FILTER mode=on sections={len(sections)} "
            f"candidates={len(candidates)} threshold={_FILTER_THRESHOLD}"
        )

        enhanced_sections: List[str] = []
        for heading, body in sections:
            # `heading` already ends with "\n" (the regex's \s*$ consumes it).
            sec_text = heading + body
            sec_cands = _filter_for_section(sec_text, candidates)
            if not sec_cands:
                logger.info(
                    f"[IMG-TRACE] SECTION_DECISION heading={heading.strip()!r} "
                    f"matched=0 chosen=0"
                )
                enhanced_sections.append(sec_text)
                continue
            out = self._run_enhance(sec_text, sec_cands)
            enhanced_sections.append(out)
            logger.info(
                f"[IMG-TRACE] SECTION_DECISION heading={heading.strip()!r} "
                f"matched={len(sec_cands)} chosen={out.count('![')}"
            )

        return "\n\n".join(enhanced_sections)