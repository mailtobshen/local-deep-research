"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from loguru import logger

from .bank import ImageBank
from .enhancer import DEFAULT_VISION_CAP, DEFAULT_VISION_MIN_ALT_TRIGGER, ImageEnhancer
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


def _sectionize(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs by `## `.

    Used by both ``extract_segment_sources`` (to map each section to its
    authoritative source URLs) and ``fill_section_images`` (to find
    image-free sections in the same iteration).
    """
    parts = re.split(r"(?m)^(##\s+.*)$", markdown)
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts), 2):
        heading = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out.append((heading, body))
    if not out and markdown.strip():
        out = [("", markdown)]
    return out


def extract_segment_sources(
    markdown: str, results, top_n: int = 3
) -> list[tuple[str, str, list[str]]]:
    """Map each ``## `` section to the top-N search_result URLs whose
    title/content/snippet overlap with the section heading + body.

    Sections whose text matches no candidate inherit the previous
    section's allow-list so an orphan section between two matched
    ones still has authoritative URLs to cite. Returns ``[]`` when
    ``results`` carries no ``search_results`` (the caller should fall
    back to global matching).
    """
    if not isinstance(results, dict):
        return []
    candidates: list[dict] = []
    for finding in results.get("findings", []) or []:
        for sr in finding.get("search_results", []) or []:
            if not isinstance(sr, dict):
                continue
            url = sr.get("link") or sr.get("url")
            if not url:
                continue
            candidates.append(
                {
                    "url": url,
                    "title": sr.get("title") or "",
                    "content": sr.get("content") or sr.get("snippet") or "",
                    "snippet": sr.get("snippet") or "",
                }
            )
    if not candidates:
        return []

    sections = _sectionize(markdown)
    out: list[tuple[str, str, list[str]]] = []
    inherited: list[str] = []
    for heading, body in sections:
        section_text = re.sub(r"^##\s+", "", heading) + "\n" + body
        section_terms = _match_terms(section_text)
        if not section_terms:
            out.append((heading, body, list(inherited)))
            continue
        scored: list[tuple[int, str]] = []
        for c in candidates:
            cand_terms = _match_terms(
                " ".join([c["title"], c["content"], c["snippet"]])
            )
            scored.append((_score_match(section_terms, cand_terms), c["url"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        # Keep URLs that score > 0; if none, inherit from the previous
        # section so an orphan section between two matched ones still
        # has authoritative URLs to cite.
        allowed = [url for score, url in scored if score > 0][:top_n]
        if not allowed:
            allowed = list(inherited)
        out.append((heading, body, allowed))
        inherited = allowed
    return out


def _filter_bank_by_segments(
    all_urls: list[str],
    bank: "ImageBank",
    markdown: str,
    results,
) -> list[str]:
    """Drop candidates whose ``source_url`` falls outside the segment
    allow-list derived from ``markdown`` + ``results``.

    When a research report covers multiple regions / topics, each
    ``##`` section maps to a small set of authoritative source URLs
    (via :func:`extract_segment_sources`). Candidates whose
    ``source_url`` does not match any segment's allow-list belong to an
    off-topic topic and would dilute the report if allowed through. This
    helper returns only the in-segment URLs so callers can swap them
    into the LLM enhancer (and, in Task 4, the ``fill_section_images``
    fallback). When ``results`` carries no ``search_results`` (no segment
    sources can be derived), the helper returns ``all_urls`` unchanged
    so callers preserve their previous global behavior. Order is
    preserved (subsequence of ``all_urls``).
    """
    segment_sources = extract_segment_sources(markdown, results)
    allowed: set[str] = set()
    for _heading, _body, urls in segment_sources:
        allowed.update(urls)
    if not allowed:
        return list(all_urls)

    def _allowed(url: str) -> bool:
        return any(
            url.startswith(prefix) or prefix in url for prefix in allowed
        )

    return [
        u for u in all_urls if _allowed(bank._by_url[u].source_url or "")
    ]


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
        s = re.sub(r"\s+", " ", s).strip()
        return s

    out = _sanitize(alt or "")
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out


def _match_terms(text: str) -> set[str]:
    """Tokenize a heading/alt for cross-language overlap scoring.

    Returns a multiset-like set containing:
      - lower-cased Roman tokens (a-z0-9 runs, length >= 2)
      - Chinese / CJK character unigrams and bigrams
      - the joined Roman-token string (so a heading like "Canton Tower"
        still matches an alt like "Guangzhou Canton Tower")

    Bigrams smooth over Chinese/Japanese/Korean text (no whitespace) and
    unigrams keep the score sensitive to single-character headings like
    "桥". The joined token form is the cheap path that catches the common
    case where one heading word is a substring of the alt's Roman words.
    """
    normalized = re.sub(r"\s+", "", text).lower()
    ascii_words = re.findall(r"[a-z0-9]+", normalized)
    ascii_words = [w for w in ascii_words if len(w) >= 2]
    non_ascii = "".join(ch for ch in normalized if ord(ch) > 127)

    terms: set[str] = set()
    terms.update(ascii_words)
    if ascii_words:
        terms.add("".join(ascii_words))

    if non_ascii:
        for ch in non_ascii:
            terms.add(ch)
        for index in range(len(non_ascii) - 1):
            terms.add(non_ascii[index : index + 2])
    return terms


def _score_match(heading_terms: set[str], alt_terms: set[str]) -> int:
    """Sum of cross-language overlap signals.

    Set intersection is the cheap baseline. The substring check on the
    joined Roman tokens (captured inside _match_terms) handles the
    common case where a heading word is a strict prefix of an alt
    token, e.g. heading "Canton" vs alt token "canton" (already covered
    by set membership once the alt token is "canton") OR
    heading "Canton" vs alt text "Guangzhou Canton Tower" (the
    joined-token term for the alt becomes "guangzhoucantontower", and
    the heading's joined token "canton" is a substring). For the latter
    case we additionally check whether any heading Roman token appears
    as a substring of any alt Roman token so the score reflects it.
    """
    score = len(heading_terms & alt_terms)
    heading_text = "".join(
        t for t in heading_terms if all(ch.isascii() for ch in t)
    )
    alt_text = "".join(t for t in alt_terms if all(ch.isascii() for ch in t))
    if heading_text and alt_text and heading_text in alt_text:
        # Substring containment is a weaker signal than token equality
        # (could be a coincidental letter match), so weight it lower.
        score += 2
    return score


def fill_section_images(markdown: str, candidates) -> str:
    """Add the best unused candidate image to each image-free ``##`` section."""
    parts = re.split(r"(?m)^(##\s+.*)$", markdown)
    used_urls = {match.group(2) for match in _IMG_RE.finditer(markdown)}

    for index in range(1, len(parts), 2):
        heading = parts[index]
        body = parts[index + 1] if index + 1 < len(parts) else ""
        if _IMG_RE.search(body):
            continue

        heading_terms = _match_terms(re.sub(r"^##\s+", "", heading))
        if not heading_terms:
            continue

        best = None
        best_score = 0
        for candidate in candidates:
            if not candidate.alt or candidate.url in used_urls:
                continue
            alt_terms = _match_terms(candidate.alt)
            score = _score_match(heading_terms, alt_terms)
            if score > best_score:
                best = candidate
                best_score = score

        if best is not None:
            parts[index] = f"{heading}\n![{best.alt}]({best.url})"
            used_urls.add(best.url)

    return "".join(parts)


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
        )
        # Cross-source filtering: drop candidates whose source_url is
        # outside the segment allow-list for clean_markdown. Done before
        # the LLM call so off-topic candidates aren't sent to the LLM in
        # the first place. ``fill_section_images`` keeps the unfiltered
        # bank for now — Task 4 widens its signature to honor the same
        # allow-list.
        segment_sources = extract_segment_sources(clean_markdown, results)
        allowed_urls: set[str] = set()
        for _heading, _body, urls in segment_sources:
            allowed_urls.update(urls)
        if allowed_urls:
            kept_urls = _filter_bank_by_segments(
                bank.all_urls(), bank, clean_markdown, results
            )
            dropped = len(bank.all_urls()) - len(kept_urls)
            logger.info(
                f"[IMG-TRACE] THEME_DROP research={research_id} "
                f"kept_in={len(allowed_urls)} segment_dropped={dropped}"
            )
            bank_for_enhancer = ImageBank()
            for u in kept_urls:
                bank_for_enhancer.add([bank._by_url[u]])
        else:
            logger.info(f"[IMG-TRACE] NO_SOURCE_META research={research_id}")
            bank_for_enhancer = bank
        enhanced = enhancer.enhance(clean_markdown, bank_for_enhancer)

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

        candidates = bank.candidates_with_alt()
        before_fallback = len([m.group(2) for m in _IMG_RE.finditer(enhanced)])
        enhanced = fill_section_images(enhanced, candidates)

        # Persist the real URLs that survived into the enhanced markdown,
        # including section fallback placements.
        chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]
        logger.info(
            f"[IMG-TRACE] section_fallback research={research_id} "
            f"placed={len(chosen) - before_fallback} considered={len(candidates)}"
        )
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
        # Pull alt + source-page metadata from the bank for each chosen URL
        # so DB records (research_images.alt/source_url/source_title) are
        # populated for post-hoc analysis (e.g. re-fetching source HTML to
        # backfill alt for past research).
        url_to_meta = {
            url: bank._by_url[url]
            for url in chosen
            if url in bank._by_url
        }
        url_to_alt = {
            u: _safe_alt(m.alt) for u, m in url_to_meta.items() if m.alt
        }
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
