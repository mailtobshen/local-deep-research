"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from loguru import logger

from .bank import ImageBank
from .reference_sanitizer import sanitize_references
from .relevance import (
    _split_sections,
    build_citation_index,
)
from .semantic_matcher import (
    DEFAULT_MIN_MARGIN as _DEFAULT_MIN_MARGIN,
    DEFAULT_THRESHOLD as _DEFAULT_THRESHOLD,
    _cosine,
    _encode_phrase_cached,
)
# Import the module for direct function access (needed for monkeypatching in tests)
from . import semantic_matcher
from .serialize import loads_images
from .store import ImageStore, _IMG_RE


def _dedupe_images(markdown: str) -> tuple[str, int, int]:
    """Collapse duplicate ``![alt](url)`` occurrences to first-only.

    The LLM prompt instructs "Each image URL may appear at most ONCE"
    (see ``enhancer._PROMPT``), but that's a prompt, not a guarantee —
    when alt text is generic (``"Image"``, ``"Photo by ..."``) the LLM
    occasionally places the same URL in 2-3 sections. This pass enforces
    uniqueness so the final markdown has each persisted image exactly
    once. We keep the FIRST occurrence (deterministic, matches LLM's
    earliest judgment) and remove later ones.

    Returns:
        (deduped_markdown, original_count, unique_count)
    """
    seen: set[str] = set()
    parts: list[str] = []
    last_end = 0
    original_count = 0
    for m in _IMG_RE.finditer(markdown):
        original_count += 1
        url = m.group(2)
        # Always emit the prose between the previous match and this one,
        # regardless of whether we keep or drop the current match.
        parts.append(markdown[last_end:m.start()])
        if url in seen:
            # Drop the duplicate match. Surrounding prose stays intact.
            # The trailing newlines may collapse and create runs of blank
            # lines, which we squeeze below.
            logger.info(
                f"[IMG-TRACE] DEDUPE_DROP alt={(m.group(1) or '')[:200]!r} "
                f"img_url={url}"
            )
        else:
            seen.add(url)
            parts.append(m.group(0))
        last_end = m.end()
    parts.append(markdown[last_end:])
    out = "".join(parts)
    # Squeeze runs of 3+ blank lines that dedup may create when the
    # duplicate was on its own line.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out, original_count, len(seen)


def _safe_alt(alt: str, max_len: int = 120) -> str:
    """Sanitize an alt string for safe markdown rendering.

    Steps (in order):
      1. Strip the ``[`` and ``]`` bracket delimiters (LLM prompt
         side-effects) while preserving their inner text content.
      2. Collapse all whitespace (incl. newlines) into single spaces.
      3. Truncate to ``max_len`` chars and append ``…`` when over the limit.
    """

    def _sanitize(s: str) -> str:
        s = s.replace("[", "").replace("]", "")
        return re.sub(r"\s+", " ", s).strip()

    out = _sanitize(alt or "")
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out


def insert_images_by_section(
    markdown: str,
    placements: list[tuple[int, str, str]],
) -> str:
    """Insert each image after its bound section's heading line.

    ``placements`` is a list of ``(section_idx, url, alt)`` tuples,
    sorted by ``section_idx``. An empty/whitespace alt skips the image
    (no useful description). Out-of-range section indices are skipped.
    The alt is cleaned via ``_safe_alt`` before rendering.
    """
    if not markdown or not placements:
        return markdown
    from .relevance import _section_offsets, _split_sections

    sections = _split_sections(markdown)
    offsets = _section_offsets(markdown)
    # Group placements by section_idx, preserving order within a section.
    by_section: dict[int, list[tuple[str, str]]] = {}
    for sidx, url, alt in placements:
        if sidx < 0 or sidx >= len(sections):
            continue
        clean_alt = _safe_alt(alt or "")
        if not clean_alt:
            continue
        by_section.setdefault(sidx, []).append((url, clean_alt))

    if not by_section:
        return markdown

    # Rebuild markdown by walking sections in order, inserting each
    # section's images right after its heading line.
    out_chunks: list[str] = []
    cursor = 0
    # offsets[i] is the absolute offset where section i's heading begins.
    for sidx in range(len(sections)):
        if sidx >= len(offsets):
            break
        sec_start = offsets[sidx]
        # Copy everything from cursor up to this section's heading.
        out_chunks.append(markdown[cursor:sec_start])
        # Find end of the heading line to insert images right after it.
        line_end = markdown.find("\n", sec_start)
        if line_end == -1:
            line_end = len(markdown)
        out_chunks.append(markdown[sec_start:line_end])
        if sidx in by_section:
            img_lines = "".join(
                f"\n\n![{alt}]({url})" for url, alt in by_section[sidx]
            )
            out_chunks.append(img_lines)
        cursor = line_end
    out_chunks.append(markdown[cursor:])
    return "".join(out_chunks)


def enhance_report_with_images(
    *,
    research_id: str,
    clean_markdown: str,
    results: Dict[str, Any],
    db_session,
    enable_images: bool,
    vision_model: str,
    vision_url: Optional[str] = None,
    vision_api_key: Optional[str] = None,
    vision_min_alt_count: Optional[int] = None,
    vision_cap: Optional[int] = None,
    firecrawl_client=None,
    alt_similarity_threshold: float = _DEFAULT_THRESHOLD,
    alt_similarity_min_margin: float = _DEFAULT_MIN_MARGIN,
) -> str:
    """Return markdown with real images inserted + mirrored locally.

    When enable_images is False, returns clean_markdown unchanged.
    """
    if not enable_images:
        return clean_markdown
    logger.info(
        f"[IMG-TRACE] BEGIN research={research_id} "
        f"mode=citation_anchored images_enabled=true"
    )
    try:
        # Stage 1: drop References rows the body never cites.
        clean_markdown = sanitize_references(clean_markdown)

        # Stage 0: build citation index from the cleaned markdown + results.
        num_to_url, section_to_nums, url_to_html = build_citation_index(
            clean_markdown, results
        )
        logger.info(
            f"[IMG-TRACE] CITATION_INDEX research={research_id} "
            f"nums={len(num_to_url)} sections={len(section_to_nums)} "
            f"html_covered={len(url_to_html)}"
        )
        if not num_to_url or not url_to_html:
            logger.info(
                f"[IMG-TRACE] BANK_EMPTY research={research_id} "
                f"reason=no_citations_or_html"
            )
            logger.info(
                f"[IMG-TRACE] END research={research_id} status=empty"
            )
            return clean_markdown

        # Per-section entity pool + embeddings for the semantic gate.
        sections = _split_sections(clean_markdown)
        entity_pool = semantic_matcher.build_report_entity_pool(clean_markdown)
        section_phrases: dict[int, str] = {}
        for sidx, entities in entity_pool.items():
            if sidx >= len(sections) or not entities:
                continue
            phrase = semantic_matcher._canonical_section_phrase(sections[sidx][0], entities)
            if phrase:
                section_phrases[sidx] = phrase
        # Pre-embed section phrases (one vector per cited section).
        try:
            section_vecs: dict[int, list[float]] = {
                sidx: list(_encode_phrase_cached(p))
                for sidx, p in section_phrases.items()
            }
        except Exception as exc:
            logger.warning(
                f"[IMG-TRACE] SEMANTIC_MATCH_FAILED research={research_id} "
                f"reason={type(exc).__name__}: {exc}"
            )
            logger.info(
                f"[IMG-TRACE] END research={research_id} status=empty"
            )
            return clean_markdown

        threshold = alt_similarity_threshold
        bank = ImageBank()
        binding: dict[str, tuple[str, int]] = {}  # url -> (num, section_idx)

        # Stage 2: extract images from each cited source, single-section
        # semantic gate against the citation's section.
        for sidx, nums in section_to_nums.items():
            if not nums or sidx not in section_vecs:
                continue
            sec_vec = section_vecs[sidx]
            for num in nums:
                url = num_to_url.get(num)
                html = url_to_html.get(url) if url else None
                if not html:
                    continue
                imgs = loads_images(html)
                if not imgs:
                    continue
                kept = 0
                dropped_low = 0
                model = semantic_matcher.get_model()
                for img in imgs:
                    if not (img.alt and img.alt.strip()):
                        logger.debug(
                            f"[IMG-TRACE] CANDIDATE_NO_ALT research={research_id} "
                            f"src_url={url} img_url={img.url}"
                        )
                        continue
                    raw = model.encode([img.alt], normalize_embeddings=True)[0]
                    alt_vec = list(raw.tolist()) if hasattr(raw, "tolist") else list(raw)
                    score = _cosine(alt_vec, sec_vec)
                    if score >= threshold:
                        # Per-image trace on the mandatory path. We
                        # carry the four fields the user asks for
                        # verbatim so log parsers (and humans tailing
                        # the stdout) can reconstruct a single
                        # (alt, image_url, source_page, cite_number)
                        # tuple from one grep hit:
                        #   img_alt       — the <img alt="..."> text
                        #   img_url       — the image's own absolute URL
                        #   img_source_url — the page the image was
                        #                   extracted from (== src_url
                        #                   and == the cited reference
                        #                   page, but spelled out for
                        #                   grep-ability)
                        #   cite_num      — the inline-citation number
                        #                   (``[N]``) in the report
                        #                   body that references this
                        #                   image's source page
                        #   ref_url       — the cited reference URL
                        #                   (== img_source_url; emitted
                        #                   under this name to make the
                        #                   "参考文献 url" semantic
                        #                   explicit)
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_KEPT research={research_id} "
                            f"img_alt={(img.alt or '')[:200]!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} "
                            f"sec={sidx} score={score:.3f}"
                        )
                        bank.add([img])
                        # First bound section wins: when the same source
                        # URL is cited in several sections, the image
                        # lands in the earliest section (matches
                        # _dedupe_images' keep-first semantics).
                        if img.url not in binding:
                            binding[img.url] = (num, sidx)
                        kept += 1
                    else:
                        logger.debug(
                            f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                            f"src_url={url} img_url={img.url} "
                            f"alt={(img.alt or '')[:200]!r} "
                            f"sec={sidx} num={num} score={score:.3f} "
                            f"reason=below_threshold"
                        )
                        dropped_low += 1
                logger.info(
                    f"[IMG-TRACE] CITATION_MATCH research={research_id} "
                    f"num={num} imgs={len(imgs)} kept={kept} "
                    f"low_similarity={dropped_low}"
                )

        if not bank.all_urls():
            logger.info(
                f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} total=0"
            )
            logger.info(
                f"[IMG-TRACE] END research={research_id} status=empty"
            )
            return clean_markdown

        bank_with_alt = len(bank.candidates_with_alt())
        bank_total = len(bank.all_urls())
        logger.info(
            f"[IMG-TRACE] BANK_FINALIZE research={research_id} "
            f"total={bank_total} with_alt={bank_with_alt} "
            f"without_alt={bank_total - bank_with_alt}"
        )
        logger.info(
            f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} "
            f"total={bank_total}"
        )

        # Stage 3: deterministic insert at each image's bound section.
        # ImageEnhancer is intentionally NOT called (paused).
        # Build placements from binding (url -> (num, section_idx)) joined
        # with the bank's images in one pass (avoid O(n^2) lookups).
        bank_by_url = {img.url: img for img in bank.candidates_with_alt()}
        placements = sorted(
            (
                (sidx, url, bank_by_url[url].alt)
                for url, (num, sidx) in binding.items()
                if url in bank_by_url
            ),
            key=lambda p: (p[0], p[1]),
        )
        for sidx, p_url, p_alt in placements:
            p_num = binding.get(p_url, (None, None))[0]
            p_src = bank_by_url[p_url].source_url
            # Same field names as CANDIDATE_KEPT so the trace schema
            # is one consistent shape from "candidate kept" through
            # "actually placed into the report". ``cite_num`` and
            # ``ref_url`` carry the (citation number, reference URL)
            # the user asked for on the mandatory path.
            logger.info(
                f"[IMG-TRACE] PLACEMENT research={research_id} "
                f"img_alt={(p_alt or '')[:200]!r} "
                f"img_url={p_url} "
                f"img_source_url={p_src} "
                f"cite_num={p_num} "
                f"ref_url={p_src} "
                f"sec={sidx}"
            )
        enhanced = insert_images_by_section(clean_markdown, placements)
        logger.info(
            f"[IMG-TRACE] INSERT research={research_id} "
            f"placements={len(placements)}"
        )

        # Stage 4: dedupe across the whole document.
        enhanced, _orig, _uniq = _dedupe_images(enhanced)

        # Persist real, mirrored image URLs (unchanged contract).
        # ImageStore(research_id, db_session, base_dir=..., firecrawl_client=None)
        # persist(urls, url_to_alt=None, url_to_source=None) -> {url: route}
        chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]
        url_to_alt = {
            img.url: img.alt
            for img in bank.candidates_with_alt()
            if img.url in chosen
        }
        url_to_source = {
            img.url: (img.source_url, img.source_title)
            for img in bank.candidates_with_alt()
            if img.url in chosen
        }
        store = ImageStore(
            research_id=research_id,
            db_session=db_session,
            firecrawl_client=firecrawl_client,
        )
        mapping = store.persist(chosen, url_to_alt, url_to_source)
        enhanced = store.rewrite_markdown(enhanced, mapping)
        logger.info(
            f"[IMG-TRACE] PERSIST research={research_id} chosen={len(chosen)}"
        )
        logger.info(
            f"[IMG-TRACE] END research={research_id} status=ok"
        )
        return enhanced
    except Exception:
        logger.exception(
            "Image post-processing failed; returning clean markdown"
        )
        logger.info(
            f"[IMG-TRACE] END research={research_id} status=error"
        )
        return clean_markdown
