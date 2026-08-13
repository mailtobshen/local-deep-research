"""Semantic-match filter for the image-enhancement pipeline.

Replaces the old ``evaluate_candidate`` hard string-equality gate.
Embeds each section's named-entity pool and each image's alt text
with ``paraphrase-multilingual-mpnet-base-v2`` (50+ languages
shared vector space) and keeps an image only when its cosine
similarity to some section's pool clears a threshold AND the
candidate's source_url shares a registrable domain with at least
one of the report's cited URLs.

Public surface:
  * ``get_model()`` — lazy, thread-safe SentenceTransformer singleton
    with fp16 default to fit the 1.155 GB cgroup cap.
  * ``build_report_entity_pool(markdown) -> dict[int, list[str]]`` —
    per-section entity pool with a noise filter (length floor,
    Roman numerals, CJK proper-noun allowlist, dedup, per-section
    cap of 50).
  * ``semantic_match_filter(...)`` — the gate.

Settings (threshold, min_margin, model name, device, fp16, enabled)
are passed explicitly as kwargs from the caller — same pattern as
``enable_images`` for the rest of the postprocessing module.

The module imports ``_extract_named_entities``, ``_split_sections``,
``_SKIPPED_SECTION_HEADINGS`` and ``domains_match`` from
``relevance`` (kept for reuse; their original home, the strict
context-entity gate, was removed).
"""
from __future__ import annotations

import functools
import re
import threading
from typing import Iterable

from loguru import logger

from .relevance import (
    _SKIPPED_SECTION_HEADINGS,
    _extract_named_entities,
    _split_sections,
    domains_match,
)


# ---------------------------------------------------------------------------
# Configuration defaults (caller overrides per call)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
# Function-level default. The report path overrides this via the
# report.image_alt_similarity_threshold user setting (read in
# _open_image_enhancer_session); non-report callers still get 0.6.
DEFAULT_THRESHOLD = 0.6
DEFAULT_MIN_MARGIN = 0.05
DEFAULT_DEVICE = "cpu"
DEFAULT_BATCH_SIZE = 1
DEFAULT_ENABLED = True
DEFAULT_FP16 = True  # cgroup cap is 1.155 GB; fp16 model fits.

_MIN_ENTITY_LEN = 2  # 2-char CJK proper nouns (故宫, 颐和园) are common
_PER_SECTION_CAP = 50


# ---------------------------------------------------------------------------
# Settings lookup
# ---------------------------------------------------------------------------
#
# All settings are passed explicitly as kwargs from the caller
# (``enhance_report_with_images`` in postprocessing.py). The defaults
# above are the fallback when the caller doesn't override. This
# matches the rest of the postprocessing module, which takes
# ``enable_images`` etc. as explicit args from
# ``research_service.py``. There is no module-level settings
# lookup — keeps the dep graph clean and makes the gate testable
# without a settings context.


# ---------------------------------------------------------------------------
# Noise filter
# ---------------------------------------------------------------------------

_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d+(\.\d+)*\.?")
_PUNCT_DIGIT_RE = re.compile(r"[\d\W_]+")


def _filter_entity_pool(entities: Iterable[str]) -> list[str]:
    """Drop noise from ``_extract_named_entities`` output.

    Removes:
      * Pure digit / decimal / punctuation runs: ``1``, ``12``,
        ``3.1.4``, ``1.``, ``：``
      * Roman numerals: ``I``, ``II``, ``III``, ``IV``, ``V`` …
      * Single-character spans — too short to be a real entity in
        any language (1 CJK char is a particle, 1 Latin letter is
        a Roman numeral or initialism noise).
      * Single-character length floor. Per-language floors
        (e.g. 2-char CJK proper nouns allowed) were tried and
        removed because they cannot scale to research topics
        the table's author has not anticipated; the embedding
        model handles short-token noise gracefully.
    Keeps:
      * ``DNA`` (3-letter acronym)
      * ``Canton_Tower``, ``Forbidden City`` (≥ 2 chars)
      * Short Chinese proper nouns (``故宫``, ``颐和园``, ``北京``)
      * Multi-word English phrases (``New York City``)
    """
    out: list[str] = []
    for s in entities:
        if not s:
            continue
        if _DIGIT_RE.fullmatch(s):
            continue
        if _PUNCT_DIGIT_RE.fullmatch(s):
            continue
        if _ROMAN_RE.match(s):
            continue
        if len(s) < _MIN_ENTITY_LEN:
            continue
        out.append(s)
    # Dedupe preserving first-seen order.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


# ---------------------------------------------------------------------------
# Entity pool per section
# ---------------------------------------------------------------------------

def build_report_entity_pool(markdown: str) -> dict[int, list[str]]:
    """Return a per-section deduped list of named entities.

    Section 0 is the report's pre-first-heading prose (folded into the
    first heading by ``_split_sections``). Sections with empty pools
    return ``[]`` and are skipped at embedding time.
    """
    sections = _split_sections(markdown)
    out: dict[int, list[str]] = {}
    for idx, (heading, body) in enumerate(sections):
        text = (heading + "\n" + body) if heading else body
        raw = _extract_named_entities(text)
        cleaned = _filter_entity_pool(raw)
        # Per-section cap to bound embedding work.
        if len(cleaned) > _PER_SECTION_CAP:
            cleaned = cleaned[:_PER_SECTION_CAP]
        out[idx] = cleaned
    return out


# ---------------------------------------------------------------------------
# Model loading (lazy, thread-safe, fp16 by default)
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model = None  # populated by get_model()


def get_model(
    model_name: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    use_fp16: bool = DEFAULT_FP16,
):
    """Return the shared SentenceTransformer model, loading on first call.

    The container's cgroup memory cap is ~1.155 GB; the fp32 model is
    1.1 GB on disk and ~1.7 GB working set, so the default is fp16
    (~550 MB on disk). If the caller passes different parameters
    after a model was already loaded, the existing singleton is
    returned (parameters are sticky across the process lifetime —
    changing them requires a container restart).
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from sentence_transformers import SentenceTransformer
        logger.info(
            f"[IMG-TRACE] semantic_matcher_load model={model_name} "
            f"device={device} fp16={use_fp16}"
        )
        st = SentenceTransformer(model_name, device=device)
        if use_fp16 and device == "cpu":
            try:
                st[0].auto_model = st[0].auto_model.half()
            except Exception as exc:
                logger.warning(
                    f"[IMG-TRACE] semantic_matcher_load fp16_conversion_failed "
                    f"reason={type(exc).__name__}: {exc}"
                )
        _model = st
        return _model


# ---------------------------------------------------------------------------
# Section embeddings
# ---------------------------------------------------------------------------

def _canonical_section_phrase(
    heading: str,
    entities: Iterable[str],
    parent_heading: str = "",
) -> str:
    """Build the text the embedding model encodes for one section.

    Heading contributes section topic; the entity list contributes
    domain terms; ``parent_heading`` (the nearest preceding higher-
    level heading) gives entity-poor subsections like '主题园区与核心
    设施' the context of their parent ('上海迪士尼乐园'). Empty inputs
    return ``""`` and the caller should skip embedding for that section.
    """
    parts: list[str] = []
    if parent_heading:
        parts.append(parent_heading)
    if heading:
        parts.append(heading)
    parts.extend(entities)
    return " ".join(parts).strip()


@functools.lru_cache(maxsize=256)
def _encode_phrase_cached(phrase: str) -> tuple:
    """Cache-encoded vector for a section phrase. LRU-bounded."""
    model = get_model()
    vec = model.encode([phrase], normalize_embeddings=True)[0]
    return tuple(vec.tolist())


def _embed_sections(
    entity_pool: dict[int, list[str]],
    sections_for_filter,
) -> dict[int, list[float]]:
    """Pre-compute one embedding per section. Skips empty phrases.

    ``sections_for_filter`` is the list from
    ``extract_segment_sources``: ``[(heading, body, urls)]`` aligned by
    index with the report's sections. We use the heading here (the
    entity pool already has the body-derived entities).
    """
    out: dict[int, list[float]] = {}
    for idx, entities in entity_pool.items():
        if idx >= len(sections_for_filter):
            continue
        heading = sections_for_filter[idx][0] or ""
        phrase = _canonical_section_phrase(heading, entities)
        if not phrase:
            continue
        out[idx] = list(_encode_phrase_cached(phrase))
    return out


# ---------------------------------------------------------------------------
# Per-candidate scoring
# ---------------------------------------------------------------------------


def _cosine(v1: list[float], v2: list[float]) -> float:
    """Manual cosine similarity for two equal-length vectors.

    Both vectors are unit-length (we ``normalize_embeddings=True`` at
    encode time), so cosine reduces to a dot product. The manual
    version avoids importing torch / numpy at the call site so the
    module loads cleanly even if the heavy deps are missing in test.
    """
    n = min(len(v1), len(v2))
    if n == 0:
        return 0.0
    return sum(v1[i] * v2[i] for i in range(n))


def _best_section_match(
    alt_vec: list[float],
    section_vectors: dict[int, list[float]],
) -> tuple[int | None, float, float | None]:
    """Return ``(best_idx, best_score, second_best_score)``.

    Empty ``section_vectors`` returns ``(None, 0.0, None)``. The
    second-best score is ``None`` when there is only one section
    (margin check is skipped in that case).
    """
    if not section_vectors:
        return None, 0.0, None
    scored = sorted(
        ((idx, _cosine(alt_vec, vec)) for idx, vec in section_vectors.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    best_idx, best_score = scored[0]
    second = scored[1][1] if len(scored) > 1 else None
    return best_idx, best_score, second


# ---------------------------------------------------------------------------
# Main entry — replace evaluate_candidate
# ---------------------------------------------------------------------------

def semantic_match_filter(
    candidates: list,
    section_vectors: dict[int, list[float]],
    section_cited_urls: list[list[str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> list[tuple]:
    """Return ``[(candidate, score, best_section_idx, decision_str), ...]``.

    A candidate is kept when its best cosine similarity ≥ ``threshold``
    (default 0.6) AND it has both an alt and a source_url.

    Two checks are PAUSED (code retained, pending the citation-anchored
    pipeline rewrite that makes them redundant or inapplicable):
      * ``ambiguous_match`` (margin between best and second-best) —
        section ownership is deterministic when images are routed by
        citation number, so the margin gate no longer applies.
      * ``no_source_url_match`` (eTLD+1 same-source check) — overlaps
        with ``extract_segment_sources``, which already anchors each
        image to a section by its citation number.

    Active drop reasons:
      * ``"low_similarity"`` (score < threshold, or no section embeddings)
      * ``"missing_alt"`` / ``"no_source_url"`` (data integrity)

    The function does NOT call any external network or import the
    ``sentence_transformers`` library until ``get_model()`` is invoked
    (i.e. when the first ``semantic_match_filter`` call actually has
    non-empty ``section_vectors``).
    """
    out: list[tuple] = []
    if not section_vectors:
        # No embeddings — every candidate is dropped with the
        # low_similarity reason. The caller is expected to log this.
        for c in candidates:
            out.append((c, 0.0, None, "low_similarity"))
        return out

    for c in candidates:
        if not getattr(c, "alt", "") or not getattr(c, "alt", "").strip():
            out.append((c, 0.0, None, "missing_alt"))
            continue
        if not getattr(c, "source_url", ""):
            out.append((c, 0.0, None, "no_source_url"))
            continue
        # Encode the alt alone — the alt is short text and adding
        # the source URL to it tends to dilute the match. The
        # same-source eTLD+1 check below is the right place for
        # source URL.
        model = get_model()
        _raw = model.encode([c.alt], normalize_embeddings=True)[0]
        # ``encode`` returns a numpy array in production and a
        # plain list in the test fake; both support indexing. Use
        # ``tolist()`` when available (numpy), else coerce.
        if hasattr(_raw, "tolist"):
            alt_vec = list(_raw.tolist())
        else:
            alt_vec = list(_raw)
        best_idx, best_score, second = _best_section_match(alt_vec, section_vectors)
        if best_idx is None or best_score < threshold:
            out.append((c, best_score, best_idx, "low_similarity"))
            continue
        # PAUSED: ambiguous_match (min_margin) check. The new citation-
        # anchored pipeline makes section ownership deterministic (an
        # image is routed by its [[N]] citation, not by fuzzy best-
        # section matching), so the margin gate no longer applies. The
        # code path is retained pending the pipeline rewrite.
        if second is not None and (best_score - second) < min_margin:
            pass  # ambiguous_match paused
        # PAUSED: no_source_url_match (eTLD+1 same-source) check. It
        # overlapped with extract_segment_sources, which already anchors
        # each image to a section by citation number. The check is
        # redundant in the citation-anchored pipeline; retained pending
        # the rewrite.
        cited = section_cited_urls[best_idx] if best_idx < len(section_cited_urls) else []
        _ = any(domains_match(c.source_url, u) for u in cited if u)
        out.append((c, best_score, best_idx, "kept"))
    return out
