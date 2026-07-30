"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from .bank import ImageBank
from .enhancer import DEFAULT_VISION_CAP, DEFAULT_VISION_MIN_ALT_TRIGGER, ImageEnhancer
from .extractor import ExtractedImage
from .relevance import (
    _split_sections,
    extract_segment_sources,
    filter_candidates_by_section_citations,
    is_skipped_section_heading,
)
from .semantic_matcher import (
    DEFAULT_MIN_MARGIN as _DEFAULT_MIN_MARGIN,
    DEFAULT_THRESHOLD as _DEFAULT_THRESHOLD,
    _canonical_section_phrase,
    _embed_sections,
    _encode_phrase_cached,
    build_report_entity_pool,
    semantic_match_filter,
)
from .serialize import loads_images
from .store import ImageStore, _IMG_RE
from .vision import VisionDescriber
from ..config.llm_config import get_llm


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
            pass
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
    logger.info(f"[IMG-TRACE] BEGIN research={research_id} images_enabled=true")
    try:
        bank = ImageBank()
        serialized_before_dedup = 0
        findings_with_images = 0
        for finding in results.get("findings", []):
            for sr in finding.get("search_results", []) or []:
                raw = sr.get("html_content")
                if raw:
                    imgs = loads_images(raw)
                    serialized_before_dedup += len(imgs)
                    if imgs:
                        findings_with_images += 1
                    bank.add(imgs)
        total = len(bank.all_urls())
        with_alt = len(bank.candidates_with_alt())
        logger.info(
            f"[IMG-TRACE] BANK research={research_id} total={total} "
            f"with_alt={with_alt} without_alt={total - with_alt} "
            f"serialized_before_dedup={serialized_before_dedup} "
            f"sr_with_images={findings_with_images}"
        )
        logger.info(
            f"[IMG-TRACE] BANK_FULL_SUMMARY research={research_id} count={total}"
        )
        if os.getenv("LDR_IMG_TRACE_BANK_SUMMARY") == "1":
            for _i, _c in enumerate(bank.candidates_with_alt()):
                logger.info(
                    "[IMG-TRACE] BANK_ENTRY research={} idx={} url={} alt={}",
                    research_id,
                    _i,
                    _c.url,
                    (_c.alt or "")[:200],
                )
            for _i, _c in enumerate(
                bank.candidates_without_alt(limit=10**9)
            ):
                logger.info(
                    "[IMG-TRACE] BANK_ENTRY_NO_ALT research={} idx={} url={}",
                    research_id,
                    _i,
                    _c.url,
                )
        if os.getenv("LDR_IMG_TRACE_CANDIDATES") == "1":
            for candidate in bank.candidates_with_alt():
                logger.info(
                    "[IMG-TRACE] CANDIDATE_JSON {}",
                    json.dumps(
                        {
                            "research_id": research_id,
                            "url": candidate.url,
                            "alt": (candidate.alt or "")[:200],
                            "source_url": (candidate.source_url or "")[:500],
                            "source_title": (candidate.source_title or "")[:200],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
        if not bank.all_urls():
            logger.info(f"[IMG-TRACE] BANK_EMPTY research={research_id}")
            logger.info(f"[IMG-TRACE] END research={research_id} status=empty")
            return clean_markdown

        # Strict context-entity gate. Every raw candidate is evaluated
        # against the current-run report context; only kept URLs flow
        # downstream to the LLM enhancer and the persistence path.
        # Vision is intentionally NOT invoked from the report path —
        # ─── semantic-match gate (replaces the old entity gate) ───
        #
        # 1. Extract per-section named-entity pools with a noise
        #    filter (length floor 3, Roman numerals, CJK proper-noun
        #    allowlist, dedup, per-section cap).
        # 2. Embed each section's pool + heading (the "canonical
        #    phrase") with paraphrase-multilingual-mpnet-base-v2.
        # 3. For every candidate, embed the alt and find the best
        #    cosine-similarity section. Apply threshold + margin +
        #    same-source URL check.

        raw_candidates = bank.candidates_with_alt()
        sections_list = list(
            extract_segment_sources(clean_markdown, results)
        )
        for idx, (heading, _body, urls) in enumerate(sections_list):
            heading_text = heading.strip() if heading else f"<no-heading-{idx}>"
            url_list = ", ".join(urls) if urls else "<none>"
            logger.info(
                f"[IMG-TRACE] SECTION_SOURCES research={research_id} "
                f"section={idx} heading={heading_text!r} urls={url_list}"
            )
        _matched = sum(1 for _, _, urls in sections_list if urls)
        _orphans = len(sections_list) - _matched
        logger.info(
            f"[IMG-TRACE] SECTION_SOURCES_SUMMARY research={research_id} "
            f"sections={len(sections_list)} matched={_matched} "
            f"orphans={_orphans}"
        )

        # Build per-section entity pools (with noise filter).
        # Skipped-headings (References / Sources / 参考文献) are
        # excluded — they have no body entity pool to score against.
        _skipped_sections = {
            idx
            for idx, (heading, _body, _urls) in enumerate(sections_list)
            if is_skipped_section_heading(heading)
        }
        if _skipped_sections:
            logger.info(
                f"[IMG-TRACE] SECTION_HEADING_SKIP "
                f"research={research_id} sections={sorted(_skipped_sections)}"
            )
        try:
            entity_pool = build_report_entity_pool(clean_markdown)
        except Exception as exc:
            logger.warning(
                f"[IMG-TRACE] SEMANTIC_MATCH_BUILD_FAILED research={research_id} "
                f"reason={type(exc).__name__}: {exc}"
            )
            entity_pool = {}

        # Per-section embeddings: only non-skipped sections whose
        # entity pool is non-empty get an embedding vector.
        section_vectors: Dict[int, list[float]] = {}
        for sidx, entities in entity_pool.items():
            if sidx in _skipped_sections:
                continue
            if not entities:
                continue
            if sidx >= len(sections_list):
                continue
            heading = sections_list[sidx][0] or ""
            # Reuse the same embed path the function uses for
            # candidates — section and alt both go through
            # ``_encode_phrase_cached`` so the embedding space is
            # identical. We call the private helper directly here
            # because we need the dict; the public filter only
            # takes a list of (idx, vec) pairs internally.
            phrase = _canonical_section_phrase(heading, entities)
            if not phrase:
                continue
            section_vectors[sidx] = list(_encode_phrase_cached(phrase))
        logger.info(
            f"[IMG-TRACE] SEMANTIC_SECTIONS_EMBEDDED research={research_id} "
            f"sections_embedded={len(section_vectors)}"
        )

        # Per-section cited URLs (for same-source check inside the filter).
        section_cited_urls = [urls for _, _, urls in sections_list]
        # Drop URLs in skipped sections — the filter would otherwise
        # try to match a candidate's source URL against them and the
        # best-section pick could land on a skipped section.
        for sidx in _skipped_sections:
            if sidx < len(section_cited_urls):
                section_cited_urls[sidx] = []

        try:
            decisions = semantic_match_filter(
                raw_candidates,
                section_vectors,
                section_cited_urls,
                threshold=alt_similarity_threshold,
                min_margin=alt_similarity_min_margin,
            )
        except Exception as exc:
            # Model load / OOM / download failure → every candidate
            # is dropped with a single reason. Caller can flip
            # ``enable_images=False`` upstream to skip the step
            # entirely.
            logger.warning(
                f"[IMG-TRACE] SEMANTIC_MATCH_FAILED research={research_id} "
                f"reason={type(exc).__name__}: {exc}"
            )
            decisions = [
                (c, 0.0, None, "matcher_unavailable")
                for c in raw_candidates
            ]

        # Log decision summary.
        reason_counts: Dict[str, int] = {}
        for _c, _s, _i, _r in decisions:
            reason_counts[_r] = reason_counts.get(_r, 0) + 1
        kept_count = sum(1 for _c, _s, _i, _r in decisions if _r == "kept")
        ordered = ", ".join(
            f"{k}={v}" for k, v in sorted(reason_counts.items())
        )
        logger.info(
            f"[IMG-TRACE] SEMANTIC_MATCH research={research_id} "
            f"raw={len(raw_candidates)} kept={kept_count} {ordered}"
        )

        kept_urls = [c.url for c, _s, _i, r in decisions if r == "kept"]
        eligible_bank = bank.subset(kept_urls)
        logger.info(
            f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} "
            f"total={len(eligible_bank.all_urls())}"
        )

        # Reconstruct _keep_per_section from the new (candidate,
        # score, best_section_idx, reason) tuples. The old
        # ``ImageRelevanceDecision.matched_sections`` field is gone
        # (evaluate_candidate is gone); the new gate's
        # ``best_section_idx`` is the single section an image is
        # routed to.
        _keep_per_section: Dict[int, list[str]] = {}
        for c, _s, sidx, r in decisions:
            if r != "kept" or sidx is None:
                continue
            _keep_per_section.setdefault(sidx, []).append(c.url)
        for _sidx in sorted(_keep_per_section):
            _urls_for_section = _keep_per_section[_sidx]
            _preview = ",".join(_urls_for_section[:5]) + (
                f" +{len(_urls_for_section) - 5}_more"
                if len(_urls_for_section) > 5
                else ""
            )
            logger.info(
                f"[IMG-TRACE] KEEP_BY_SECTION research={research_id} "
                f"section={_sidx} kept={len(_urls_for_section)} urls={_preview}"
            )
        logger.info(
            f"[IMG-TRACE] KEEP_BY_SECTION_SUMMARY research={research_id} "
            f"sections_with_keep={len(_keep_per_section)} "
            f"total_kept={len(kept_urls)}"
        )
        logger.info(
            f"[IMG-TRACE] SECTION_FALLBACK research={research_id} "
            f"status=disabled"
        )
        if not eligible_bank.all_urls():
            logger.info(f"[IMG-TRACE] END research={research_id} status=empty")
            return clean_markdown

        llm = get_llm()
        vision = VisionDescriber(
            model_name=vision_model,
            base_url=vision_url,
            api_key=vision_api_key,
        )
        enhancer = ImageEnhancer(
            llm,
            vision,
            min_alt_count=(
                vision_min_alt_count
                if vision_min_alt_count is not None
                else DEFAULT_VISION_MIN_ALT_TRIGGER
            ),
            cap=vision_cap if vision_cap is not None else DEFAULT_VISION_CAP,
            allow_vision_fill=False,
        )
        # --- NEW: build per-section filtered candidate pool (eTLD+1) ---
        sections_for_filter = list(
            extract_segment_sources(clean_markdown, results)
        )
        # urls per section for the single-pass domain filter. The list
        # is aligned with _split_sections' output by the drift guard
        # below.
        section_urls_list = [urls for _, _, urls in sections_for_filter]

        # Section-index drift guard. extract_segment_sources and the
        # enhancer's _split_sections MUST align (both call _split_sections
        # internally). Mismatch → log + fall back to legacy full-pool.
        _n_sections_split = len(_split_sections(clean_markdown))
        if _n_sections_split != len(sections_for_filter):
            logger.warning(
                f"[IMG-TRACE] SECTION_INDEX_DRIFT "
                f"split_sections={_n_sections_split} "
                f"citations_sections={len(sections_for_filter)} — "
                f"falling back to global candidate pool"
            )
            per_section_candidates = None
        else:
            per_section_candidates: Dict[int, List[ExtractedImage]] = {}
            _total_dropped_no_source = 0
            _total_dropped_domain_mismatch = 0
            # Headings whose section must be skipped (References /
            # Sources / 参考文献). Their SECTION_FILTER_SUMMARY log line
            # is suppressed and their per-section pool is forced empty
            # so the enhancer's existing SECTION_SKIP path takes over.
            _skipped_sections = {
                sidx
                for sidx, (heading, _body, _urls) in enumerate(sections_for_filter)
                if is_skipped_section_heading(heading)
            }
            if _skipped_sections:
                logger.info(
                    f"[IMG-TRACE] SECTION_HEADING_SKIP "
                    f"research={research_id} sections={sorted(_skipped_sections)}"
                )
            # Per-section cited-domain count, populated alongside the
            # filter pass. Used by the IMG-TRACE log without an
            # independent recomputation.
            _section_cited_domain_count: Dict[int, int] = {}
            for sidx, urls in _keep_per_section.items():
                if sidx in _skipped_sections:
                    # References/Sources sections get an empty pool so
                    # the enhancer logs SECTION_SKIP reason=empty_pool
                    # for them (consistent with other no-image sections)
                    # and no SECTION_FILTER_SUMMARY line is emitted.
                    per_section_candidates[sidx] = []
                    continue
                pool: list[ExtractedImage] = []
                for u in urls:
                    img = eligible_bank._by_url.get(u)
                    if img is not None:
                        pool.append(img)
                if not pool:
                    per_section_candidates[sidx] = []
                    _section_cited_domain_count[sidx] = 0
                    continue
                # Single-pass filter: section's cited URLs → eTLD+1 set
                # ∩ candidate's source_url eTLD+1. Fail-closed on
                # unparseable URLs (no_source drop).
                section_citations = section_urls_list[sidx]
                (
                    kept,
                    dropped_no_source,
                    dropped_domain_mismatch,
                    cited_domain_count,
                ) = filter_candidates_by_section_citations(
                    pool, section_citations, section_idx=sidx
                )
                per_section_candidates[sidx] = kept
                _section_cited_domain_count[sidx] = cited_domain_count
                _total_dropped_no_source += dropped_no_source
                _total_dropped_domain_mismatch += dropped_domain_mismatch
            # Sections with no _keep_per_section entry also need a
            # (possibly empty) entry so the IMG-TRACE log covers all.
            for sidx in range(_n_sections_split):
                per_section_candidates.setdefault(sidx, [])

            for sidx in sorted(per_section_candidates):
                logger.info(
                    f"[IMG-TRACE] PER_SECTION_CANDIDATES research={research_id} "
                    f"section={sidx} candidates_in_section="
                    f"{len(per_section_candidates[sidx])} "
                    f"domains_in_section="
                    f"{_section_cited_domain_count.get(sidx, 0)}"
                )
            _total_after = sum(
                len(v) for v in per_section_candidates.values()
            )
            _sections_with_cands = sum(
                1 for v in per_section_candidates.values() if v
            )
            logger.info(
                f"[IMG-TRACE] PER_SECTION_CANDIDATES_SUMMARY research={research_id} "
                f"sections={len(per_section_candidates)} "
                f"sections_with_candidates={_sections_with_cands} "
                f"total_candidate_url_pairs={_total_after} "
                f"total_dropped_domain_mismatch={_total_dropped_domain_mismatch} "
                f"total_dropped_no_source={_total_dropped_no_source}"
            )

        enhanced = enhancer.enhance(
            clean_markdown,
            eligible_bank,
            per_section_candidates=per_section_candidates,
        )

        # Enforce "each URL at most once" deterministically. The LLM
        # prompt says it but doesn't guarantee it — generic alt text
        # ("Image", "Photo by ...") sometimes causes the same URL to
        # appear in 2-3 sections. See _dedupe_images docstring.
        enhanced, _orig_count, _unique_count = _dedupe_images(enhanced)
        if _orig_count != _unique_count:
            logger.info(
                f"[IMG-TRACE] DEDUPE research={research_id} "
                f"removed={_orig_count - _unique_count} unique={_unique_count}"
            )

        # Persist the real URLs that survived into the enhanced markdown.
        chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]
        # Drop any URL the enhancer surfaced that is NOT in the eligible
        # bank — the gate already said "no", and persisting it would
        # silently re-introduce the candidate the gate rejected.
        eligible_urls = set(eligible_bank.all_urls())
        kept_chosen = [u for u in chosen if u in eligible_urls]
        dropped_chosen = [u for u in chosen if u not in eligible_urls]
        if dropped_chosen:
            logger.info(
                f"[IMG-TRACE] CHOSEN_DROP research={research_id} "
                f"count={len(dropped_chosen)}"
            )
        chosen = kept_chosen
        _shown = ",".join(chosen[:10]) + (
            f" +{len(chosen) - 10}_more" if len(chosen) > 10 else ""
        )
        logger.info(
            f"[IMG-TRACE] ENHANCE research={research_id} "
            f"chosen={len(chosen)} urls={_shown}"
        )
        store = ImageStore(
            research_id, db_session, firecrawl_client=firecrawl_client
        )
        # Pull alt + source-page metadata from the eligible bank for each
        # chosen URL so DB records (research_images.alt/source_url/
        # source_title) are populated for post-hoc analysis (e.g.
        # re-fetching source HTML to backfill alt for past research).
        url_to_meta = {
            url: eligible_bank._by_url[url]
            for url in chosen
            if url in eligible_bank._by_url
        }
        url_to_alt = {u: m.alt for u, m in url_to_meta.items() if m.alt}
        url_to_source = {
            u: (m.source_url, m.source_title)
            for u, m in url_to_meta.items()
            if m.source_url
        }
        url_to_route = store.persist(
            chosen, url_to_alt=url_to_alt, url_to_source=url_to_source
        )
        logger.info(
            f"[IMG-TRACE] PERSIST research={research_id} chosen={len(chosen)} "
            f"persisted={len(url_to_route)} failed={len(chosen) - len(url_to_route)}"
        )
        if url_to_route:
            # Pass PIL-measured sizes from persist() so rewrite_markdown can
            # inject width/height="600" into oversized <img> tags.
            enhanced = store.rewrite_markdown(
                enhanced, url_to_route,
                url_to_size=getattr(store, "_last_url_to_size", None),
            )
        logger.info(f"[IMG-TRACE] END research={research_id} status=ok")
        return enhanced
    except Exception:
        logger.exception(
            "Image post-processing failed; returning clean markdown"
        )
        logger.info(f"[IMG-TRACE] END research={research_id} status=error")
        return clean_markdown
