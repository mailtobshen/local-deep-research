"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from loguru import logger

from .bank import ImageBank
from .enhancer import DEFAULT_VISION_CAP, DEFAULT_VISION_MIN_ALT_TRIGGER, ImageEnhancer
from .relevance import build_report_entity_context, evaluate_candidate
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
        context = build_report_entity_context(
            clean_markdown, results, query=query
        )
        raw_candidates = bank.candidates_with_alt()
        decisions = [evaluate_candidate(c, context) for c in raw_candidates]
        reason_counts: Dict[str, int] = {}
        for d in decisions:
            reason_counts[d.reason] = reason_counts.get(d.reason, 0) + 1
        kept_urls = [d.url for d in decisions if d.status == "keep"]
        logger.info(
            f"[IMG-TRACE] ENTITY_GATE research={research_id} "
            f"raw={len(raw_candidates)} kept={len(kept_urls)} "
            f"reasons={dict(sorted(reason_counts.items()))}"
        )
        eligible_bank = bank.subset(kept_urls)
        logger.info(
            f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} "
            f"total={len(eligible_bank.all_urls())}"
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
        enhanced = enhancer.enhance(clean_markdown, eligible_bank)

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
