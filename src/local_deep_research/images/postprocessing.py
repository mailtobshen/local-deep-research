"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import re
from pathlib import Path
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

    The multi-bind placement pass above may produce multiple
    ``![alt](url)`` instances for the same URL when the image's
    source page is cited in several sections. This pass enforces
    one-per-URL uniqueness so the final markdown shows each persisted
    image exactly once. We keep the FIRST occurrence
    (deterministic, matches LLM's earliest judgment — and now also
    the natural reading order) and remove later ones.

    The DEDUPE_KEEP / DEDUPE_DROP / DEDUPE_SUMMARY events share
    the same five-key schema as the upstream per-image events so a
    log parser can reconstruct the final state from one grep hit.

    Returns:
        (deduped_markdown, original_count, unique_count)
    """
    seen: set[str] = set()
    parts: list[str] = []
    last_end = 0
    original_count = 0
    dropped_count = 0
    for m in _IMG_RE.finditer(markdown):
        original_count += 1
        url = m.group(2)
        alt = m.group(1) or ""
        # Always emit the prose between the previous match and this
        # one, regardless of whether we keep or drop the current
        # match.
        parts.append(markdown[last_end:m.start()])
        if url in seen:
            # Drop the duplicate match. Surrounding prose stays
            # intact. The trailing newlines may collapse and create
            # runs of blank lines, which we squeeze below.
            dropped_count += 1
            logger.info(
                f"[IMG-TRACE] DEDUPE_DROP alt={alt[:200]!r} "
                f"img_url={url}"
            )
        else:
            seen.add(url)
            parts.append(m.group(0))
            logger.info(
                f"[IMG-TRACE] DEDUPE_KEEP alt={alt[:200]!r} "
                f"img_url={url}"
            )
        last_end = m.end()
    parts.append(markdown[last_end:])
    out = "".join(parts)
    # Squeeze runs of 3+ blank lines that dedup may create when the
    # duplicate was on its own line.
    out = re.sub(r"\n{3,}", "\n\n", out)
    logger.info(
        f"[IMG-TRACE] DEDUPE_SUMMARY original={original_count} "
        f"kept={len(seen)} dropped={dropped_count}"
    )
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
        # binding maps an image URL to every (cite_num, section_idx)
        # pair where the image was selected for placement. A single
        # URL can bind to multiple sections (e.g. the same Wikipedia
        # "Shanghai" infobox appears under both the "Bund" section
        # and the "City overview" section if both cite the same URL).
        # The post-insert dedup pass below removes any duplicate
        # ``![alt](url)`` instances the multi-bind produced — keep
        # first occurrence by document position. Allowing multi-bind
        # (instead of collapsing at this stage) gives the insertion
        # phase a chance to place the image next to every section
        # that actually cites its source page.
        binding: dict[str, list[tuple[str, int]]] = {}

        # Stage 2: extract images from each cited source, single-section
        # semantic gate against the citation's section.
        for sidx, nums in section_to_nums.items():
            if not nums or sidx not in section_vecs:
                continue
            sec_vec = section_vecs[sidx]
            # Pre-canonicalised section phrase (heading + entities
            # joined with spaces). Captured here so the CANDIDATE_SCORED
            # event below can log the original text the model encoded
            # for this section, instead of an opaque hash. Truncated
            # to 200 chars so a long entity list doesn't blow up the
            # log.
            sec_phrase_text = (section_phrases.get(sidx) or "")[:200]
            for num in nums:
                url = num_to_url.get(num)
                html = url_to_html.get(url) if url else None
                if not html:
                    continue
                imgs = loads_images(html)
                if not imgs:
                    continue
                # Emit per-(cite, section) candidate list BEFORE we
                # run any scoring. Useful for post-mortem analysis:
                # how many images did each cited URL yield, and what
                # were their alt/source_url tuples. Only candidates
                # with a non-empty alt are listed — they are the only
                # ones that will go on to the CANDIDATE_SCORED /
                # _KEPT / _DROPPED pipeline below. Empty-alt
                # candidates are emitted separately as
                # CANDIDATE_NO_ALT (debug). Field schema: cite_num,
                # ref_url, sec, count, then per-image
                # img_alt / img_url / img_source_url.
                cand_lines = []
                for cand in imgs:
                    if not (cand.alt and cand.alt.strip()):
                        # Empty-alt candidate — skip the verbose
                        # CITATION_CANDIDATES line; the per-image
                        # CANDIDATE_NO_ALT event below records it.
                        continue
                    cand_lines.append(
                        f"img_alt={(cand.alt or '')[:120]!r} "
                        f"img_url={cand.url} "
                        f"img_source_url={cand.source_url}"
                    )
                logger.info(
                    f"[IMG-TRACE] CITATION_CANDIDATES research={research_id} "
                    f"cite_num={num} ref_url={url} sec={sidx} "
                    f"count={len(cand_lines)} "
                    + ("| ".join(cand_lines) if cand_lines else "(no_alt_candidates)")
                )
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
                    # Emit the raw inputs that feed the cosine call:
                    # the image's alt text and the section's canonical
                    # phrase text (heading + entities joined). The
                    # vector itself is NOT logged — loguru lines
                    # would explode to many KB per image — but the
                    # exact strings let consumers re-run the cosine
                    # offline or audit whether the gate picked the
                    # right section for the right image. Both fields
                    # are truncated to 200 chars to keep log lines
                    # compact.
                    logger.info(
                        f"[IMG-TRACE] CANDIDATE_SCORED research={research_id} "
                        f"img_alt={(img.alt or '')[:200]!r} "
                        f"img_url={img.url} "
                        f"img_source_url={img.source_url} "
                        f"cite_num={num} ref_url={url} sec={sidx} "
                        f"sec_phrase_text={sec_phrase_text!r}"
                    )
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
                        # Multi-bind: this image can be placed in
                        # every section whose cite matches its source
                        # URL. The post-insert dedup pass below
                        # removes any duplicate ``![alt](url)`` the
                        # multi-bind produced. We do NOT collapse at
                        # this stage because the same image may
                        # legitimately belong to several sections
                        # whose relevance-gate happened to clear
                        # independently.
                        binding.setdefault(img.url, []).append(
                            (num, sidx)
                        )
                        kept += 1
                    else:
                        # Same five-key schema as CANDIDATE_KEPT so a
                        # log parser can union the two streams into a
                        # complete per-image decision table. Drops get
                        # ``kept=0`` reason baked in so consumers don't
                        # need to fork on the event name to know what
                        # happened.
                        logger.debug(
                            f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                            f"img_alt={(img.alt or '')[:200]!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} "
                            f"sec={sidx} score={score:.3f} "
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
        # Build placements from binding (url -> list[(num, sec)]) —
        # multi-bind semantics: a single image URL can bind to several
        # sections if its source page is cited in each. We emit one
        # placement per (url, sec) pair, sorted by section index for
        # stable in-section ordering. The post-insert dedup pass
        # (``_dedupe_images`` below) collapses any duplicate
        # ``![alt](url)`` instances produced by the multi-bind,
        # keeping the FIRST occurrence in document order.
        bank_by_url = {img.url: img for img in bank.candidates_with_alt()}
        placements = sorted(
            (
                (sidx, url, bank_by_url[url].alt)
                for url, pairs in binding.items()
                if url in bank_by_url
                for _num, sidx in pairs
            ),
            key=lambda p: (p[0], p[1]),
        )
        for sidx, p_url, p_alt in placements:
            # Find the (num, sec) pair for this placement. If the
            # URL is bound to multiple (num, sec), pick the one that
            # matches this placement's section.
            matching = [
                (n, sec) for n, sec in binding.get(p_url, [])
                if sec == sidx
            ]
            p_num = matching[0][0] if matching else None
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
        enhanced = store.rewrite_markdown(
            enhanced, mapping, url_to_source=url_to_source
        )
        # The aggregate ``PERSIST chosen=N`` line records how many
        # images we tried to write to disk; the per-image
        # ``PERSISTED_IMG`` lines below carry the full provenance for
        # each one so the log can answer "where did this image come
        # from, what was its alt, which citation references it, what
        # was the disk route" with a single grep — without re-reading
        # the store.
        #
        # Fix #10: also surface the count of URLs that FAILED to
        # persist. rewrite_markdown already drops them from the
        # markdown (REWRITE_DROP reason=no_local_route), but the
        # caller wants to know at a glance how many survived vs how
        # many got anti-hotlinked or 404'd. The aggregate
        # PERSIST_BROKEN_LINKS event makes this greppable.
        failed_persist = [u for u in chosen if not mapping.get(u)]
        succeeded_count = len(chosen) - len(failed_persist)
        logger.info(
            f"[IMG-TRACE] PERSIST research={research_id} "
            f"chosen={len(chosen)} succeeded={succeeded_count} "
            f"failed={len(failed_persist)}"
        )
        if failed_persist:
            logger.warning(
                f"[IMG-TRACE] PERSIST_BROKEN_LINKS research={research_id} "
                f"count={len(failed_persist)} "
                f"urls={failed_persist[:5]!r}"
            )
        chosen_img_by_url = {
            img.url: img for img in bank.candidates_with_alt()
            if img.url in chosen
        }
        for url in chosen:
            img = chosen_img_by_url.get(url)
            if img is None:
                continue
            # binding[url] is a list of (num, sidx) pairs. Pick the
            # FIRST pair (matches dedup_images' keep-first semantic
            # for the displayed image — the persisted image is the
            # one whose citation the reader saw in the body).
            pairs = binding.get(url) or [(None, None)]
            num = pairs[0][0]
            route = mapping.get(url) or ""
            # ``local_path`` is best-effort: the real ImageStore
            # exposes ``base_dir``; a test stub might not. Never let
            # the trace emission itself raise — that would mask the
            # PERSIST outcome and the inventory becomes incomplete.
            local_path = ""
            if route:
                try:
                    base_dir = getattr(store, "base_dir", None)
                    if base_dir is not None:
                        local_path = (
                            Path(base_dir) / research_id /
                            Path(route).name
                        )
                except Exception:
                    local_path = ""
            logger.info(
                f"[IMG-TRACE] PERSISTED_IMG research={research_id} "
                f"img_alt={(img.alt or '')[:200]!r} "
                f"img_url={img.url} "
                f"img_source_url={img.source_url} "
                f"cite_num={num} "
                f"ref_url={img.source_url} "
                f"local_route={route} "
                f"local_path={local_path}"
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
