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
    ImageRelevanceDecision,
    _split_sections,
    build_report_entity_context,
    evaluate_candidate,
    extract_segment_sources,
    filter_candidates_by_section_citations,
    is_skipped_section_heading,
)

ENTITY_REASON_KEYS: tuple[str, ...] = (
    "keep_context_match",
    "drop_missing_alt",
    "drop_no_named_entity",
    "drop_entity_extraction_failed",
    "drop_foreign_entity_conflict",
    "drop_unrelated_named_entity",
    "drop_unresolved_entity_relation",
    "drop_source_url_not_cited",
    "drop_context_build_failed",
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
        # candidates whose alt was rejected by the gate are not re-
        # described behind the gate's back.
        query = ""
        if isinstance(results, dict):
            query = results.get("research_query") or ""
        try:
            context = build_report_entity_context(
                clean_markdown, results, query=query
            )
        except Exception as exc:  # ContextBuildFailed or unexpected
            logger.warning(
                f"[IMG-TRACE] ENTITY_GATE_BUILD_FAILED research={research_id} "
                f"reason={type(exc).__name__}: {exc}"
            )
            context = None
        else:
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
        raw_candidates = bank.candidates_with_alt()
        if context is None:
            decisions = [
                ImageRelevanceDecision(
                    url=c.url,
                    status="drop",
                    reason="drop_context_build_failed",
                    entities=frozenset(),
                    matched_sections=frozenset(),
                    source_signal="none",
                    evidence_refs=(),
                )
                for c in raw_candidates
            ]
        else:
            decisions = [evaluate_candidate(c, context) for c in raw_candidates]
        reason_counts: Dict[str, int] = {k: 0 for k in ENTITY_REASON_KEYS}
        for d in decisions:
            if d.status == "keep":
                bucket = "keep_context_match" if d.reason == "context_match" else "keep_context_rescue"
            else:
                bucket = d.reason if d.reason in reason_counts else "drop_unrelated_named_entity"
            reason_counts[bucket] += 1
        kept_urls = [d.url for d in decisions if d.status == "keep"]
        ordered_reasons = ", ".join(
            f"{name}={reason_counts[name]}" for name in ENTITY_REASON_KEYS
        )
        logger.info(
            f"[IMG-TRACE] ENTITY_GATE research={research_id} "
            f"raw={len(raw_candidates)} kept={len(kept_urls)} "
            f"{ordered_reasons}"
        )
        eligible_bank = bank.subset(kept_urls)
        logger.info(
            f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} "
            f"total={len(eligible_bank.all_urls())}"
        )
        _keep_per_section: Dict[int, list[str]] = {}
        for _d in decisions:
            if _d.status != "keep":
                continue
            for _sidx in _d.matched_sections:
                _keep_per_section.setdefault(_sidx, []).append(_d.url)
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
