"""Strict context-entity image relevance gate.

Fail-closed decisions for candidate images: only keep a candidate when its
named entities are explicitly confirmed by the current-run report context
(query, headings, section text, and search-result metadata). No Vision
calls, no external NER/knowledge base.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, Literal, Tuple
from urllib.parse import urlparse

import tldextract
from loguru import logger

from .extractor import ExtractedImage
from ..text_optimization.citation_formatter import (
    CITE_INLINE_RE,
    CITE_INLINE_GROUP_RE,
    CITE_LIST_ROW_RE,
    find_sources_section,
)

_TLDEX = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)


# ---------------------------------------------------------------------------
# Generic vocabulary — words/phrases that NEVER count as named entities on
# their own. Matched against individual tokens (and against multi-token
# compound generics as full substrings).
# ---------------------------------------------------------------------------

_GENERIC_TOKENS: FrozenSet[str] = frozenset(
    {
        # CJK
        "旅游",
        "景点",
        "攻略",
        "建筑",
        "夜景",
        "美食",
        "交通",
        "推荐",
        "图片",
        "精彩",
        "热门",
        "必去",
        "城市",
        "古",
        "寺庙",
        "瀑布",
        "村落",
        "观光",
        # English
        "tour",
        "tours",
        "tourism",
        "attraction",
        "attractions",
        "sight",
        "sights",
        "sightseeing",
        "guide",
        "guides",
        "travel",
        "travels",
        "trip",
        "trips",
        "tip",
        "tips",
        "photo",
        "photos",
        "photography",
        "image",
        "images",
        "picture",
        "pictures",
        "food",
        "traffic",
        "transport",
        "shopping",
        "recommend",
        "recommendation",
        "recommendations",
        "popular",
        "best",
        "must",
        "visit",
        "city",
        "cities",
        "ancient",
        "temple",
        "waterfall",
        "village",
        "villages",
        "church",
        "churches",
        "night",
        "view",
    }
)

# Compound generic phrases: any alt whose tokens (after stripping generics)
# are empty AND whose original substring equals one of these is treated as
# purely-generic and rejected outright.
_COMPOUND_GENERIC_PHRASES: FrozenSet[str] = frozenset(
    {
        "旅游景点攻略",
        "旅游攻略",
        "热门景点",
        "精彩图片",
        "城市夜景",
        "夜景",
        "古建筑",
        "古村落",
        "寺庙",
        "瀑布",
        "美食地图",
        "交通指南",
        "必去景点",
        "购物推荐",
        # Common English compounds
        "travel guide",
        "travel tips",
        "must visit",
        "best attractions",
        "city view",
        "night view",
    }
)

# Allowed "connector" tokens that may appear inside compound generics
# without making the phrase look like a real named entity. We strip these
# before checking whether a token is generic-only.
_STRIP_TOKENS: FrozenSet[str] = frozenset(
    {"的", "和", "与", "或", "the", "a", "an", "of", "for", "in", "on"}
)

# Vague qualifiers that turn a downstream proper-name look like a real
# place/name when it is actually descriptive ("some place's 中山纪念堂").
# Spans whose leftmost tokens begin with one of these prefixes are not
# treated as real proper names and therefore do not trigger a foreign
# entity conflict.
_VAGUE_QUALIFIER_PREFIXES: FrozenSet[str] = frozenset(
    {"某地", "某处", "某城", "某景点", "某景区", "某地", "某区域"}
)


# ---------------------------------------------------------------------------
# Token / phrase helpers
# ---------------------------------------------------------------------------

# Word-tokenisation for CJK + Latin. CJK characters each count as their
# own token; runs of Latin letters/digits count as a token.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'_-]*|[一-鿿]")

# Patterns that mark a relationship between two entities in the report text.
# We use a manual scan for `位于` / `在` between two CJK/alnum runs so
# greedy subject matches don't swallow the particle.
_LOC_RUN = re.compile(r"[一-鿿A-Za-z0-9]{2,}")

# `Y的X` — Y's X. Matches `广州的中山纪念堂` → (广州, 中山纪念堂).
_DE_PHRASE_RE = re.compile(r"([一-鿿A-Za-z0-9]{2,})对?的([一-鿿A-Za-z0-9]{2,})")


def _is_run_char(ch: str) -> bool:
    """True if `ch` is a CJK ideograph, Latin letter, or digit."""
    if not ch:
        return False
    if ch.isalnum():
        # Treat ASCII alnum as part of a run.
        return ch.isascii() or "一" <= ch <= "鿿"
    return False


def _entity_link(a: str, b: str) -> bool:
    """True if `a` is the same as `b`, contains it, or is contained in it."""
    return bool(a and b and (a in b or b in a))


def _find_loc_relations(text: str) -> Iterable[Tuple[str, str]]:
    """Yield (subject, location) pairs from `位于`/`在` constructions."""
    if not text:
        return
    for particle in ("位于", "在"):
        idx = 0
        while True:
            j = text.find(particle, idx)
            if j < 0:
                break
            left_start = j
            while left_start > 0 and _is_run_char(text[left_start - 1]):
                left_start -= 1
            subj = text[left_start:j].strip()
            right_start = j + len(particle)
            while right_start < len(text) and text[right_start].isspace():
                right_start += 1
            right_end = right_start
            while right_end < len(text) and _is_run_char(text[right_end]):
                right_end += 1
            loc = text[right_start:right_end].strip()
            if subj and loc and subj != loc:
                yield subj, loc
            idx = j + len(particle)


def _tokens(text: str) -> list[str]:
    """Tokenize CJK + Latin text."""
    return _TOKEN_RE.findall(text or "")


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _extract_registered_domain(url: str) -> str:
    """Return eTLD+1 ('ctrip.com', 'bbc.co.uk', 'github.io') for a URL.

    Returns "" on any failure (malformed URL, tldextract error, missing
    hostname, embedded control characters, leading/trailing whitespace).
    Callers MUST treat "" as "unknown" and conservatively reject
    candidates whose domain could not be determined, to keep the
    per-section same-domain filter fail-closed.
    """
    if not url:
        return ""
    # Strip whitespace first — "  https://x.com  " otherwise has its
    # scheme tokenised by tldextract and returns "https" as the
    # registered domain (real bug observed during boundary testing).
    stripped = url.strip()
    # Reject embedded NUL/control bytes — they confuse urlparse downstream
    # and we never expect them in real search-result URLs.
    if any(ord(c) < 0x20 for c in stripped):
        logger.debug(
            f"[IMG-TRACE] DOMAIN_EXTRACT url={url!r} "
            f"reason=control_char_in_url"
        )
        return ""
    try:
        ext = _TLDEX(stripped)
    except Exception as exc:  # tldextract raises on bizarre inputs
        logger.debug(
            f"[IMG-TRACE] DOMAIN_EXTRACT url={url!r} reason=tldextract_error "
            f"exc={type(exc).__name__}"
        )
        return ""
    reg = ".".join(p for p in (ext.domain, ext.suffix) if p)
    return reg.lower()


def domains_match(url_a: str, url_b: str) -> bool:
    """True when the two URLs share the same eTLD+1 (registrable domain).

    Used for same-source checks between a section's cited URLs and a
    candidate image's ``source_url``. eTLD+1 is the right granularity:

    * ``a1.ctrip.com/x`` and ``b.ctrip.com/y`` both reduce to
      ``ctrip.com`` → match. This is what we want for distributed
      image CDNs that all sit under the same operator.
    * ``https://en.wikipedia.org/wiki/Canton_Tower`` and
      ``https://zh.wikipedia.org/wiki/广州塔`` both reduce to
      ``wikipedia.org`` → match. Subdomain variants of the same
      project are treated as the same source.
    * ``wikipedia.org`` and ``pedia.org`` both have ``org`` as their
      public suffix but reduce to ``wikipedia.org`` and ``pedia.org``
      respectively → no match. The shared-label overlap (``pedia``)
      is purely a substring accident.
    * ``bbc.co.uk`` and ``bbc.com`` reduce to ``bbc.co.uk`` and
      ``bbc.com`` → no match. Different registrable units.

    Fail-closed: if either URL cannot be parsed (returns ``""`` from
    ``_extract_registered_domain``) the function returns False. This
    keeps a malformed URL from sneaking through the filter; the
    conservative outcome is that the candidate is rejected.

    Empty inputs return False.
    """
    if not url_a or not url_b:
        return False
    a = _extract_registered_domain(url_a)
    b = _extract_registered_domain(url_b)
    if not a or not b:
        return False
    return a == b


def _is_compound_generic(alt: str) -> bool:
    """True if the alt is purely composed of generic tokens.

    Two ways to qualify:
    1. Exact-match against the curated compound-generic phrase list.
    2. Every token in the alt (after stripping connectors) is in the
       generic vocabulary, AND the alt is longer than a single character.
    """
    if not alt:
        return False
    stripped = alt.strip()
    if stripped in _COMPOUND_GENERIC_PHRASES:
        return True
    toks = [t for t in _tokens(stripped) if t not in _STRIP_TOKENS]
    if not toks:
        return False
    # Reject if every token is generic. We require len>=2 tokens OR a
    # multi-character CJK span made entirely of generic characters (e.g.
    # `城市夜景` is 4 generic chars).
    cjk_chars = [t for t in toks if re.fullmatch(r"[一-鿿]", t)]
    if cjk_chars and all(c in _GENERIC_TOKENS for c in cjk_chars):
        if len(cjk_chars) >= 2:
            return True
    if len(toks) >= 2 and all(t in _GENERIC_TOKENS for t in toks):
        return True
    return False


# ---------------------------------------------------------------------------
# Section parsing of the report markdown
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(markdown: str) -> list[Tuple[str, str]]:
    """Split markdown into (heading, body) tuples, one per `^#{1,6}` line.

    Section index 0 is the first heading in the document. Any prose
    before the first heading is folded into the first section's body so
    callers can iterate sections with a simple integer index starting
    at 0.
    """
    if not markdown:
        return []
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return [("", markdown.strip())]
    sections: list[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end() if i == 0 else m.end()
        # Fold any pre-first-heading prose into the first section's body.
        if i == 0:
            pre = markdown[: m.start()].strip()
            start = m.end()
        else:
            pre = ""
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if pre:
            body = (pre + "\n\n" + body).strip()
        sections.append((heading, body))
    return sections


def _section_offsets(markdown: str) -> list[int]:
    """Char offset of each section's heading start in the original markdown.

    Aligned with ``_split_sections``: ``section_offsets[i]`` is the char
    position of the i-th heading's start. The body slice for section ``i``
    is then ``markdown[section_offsets[i] : section_offsets[i+1]]``
    (or to the end of the document for the last section). Using the
    heading start (not the heading end) keeps the heading line itself
    inside the body slice — which is fine because the citation pattern
    only matches ``[N]`` tokens, not heading text.

    For a document with no headings at all, ``_split_sections`` returns
    a single implicit section with body = full markdown, and we return
    ``[0]`` to match.
    """
    if not markdown:
        return []
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return [0]
    return [m.start() for m in matches]


def _scan_references_block(markdown: str) -> Dict[str, str]:
    """Parse the trailing References/Sources/参考文献 block.

    Returns ``{citation_number_str: url}``. Returns an empty dict when
    the markdown has no References heading. Comma groups like
    ``[1, 2] URL: ...`` produce entries for each individual number.
    Rows without a URL line are skipped (the section can never cite
    a URL it does not know about).

    English headings (``## References`` etc.) are matched by
    ``find_sources_section``. CJK headings (``## 参考文献`` /
    ``## 参考资料`` / ``## 引用来源``) are matched here against
    ``_SKIPPED_SECTION_HEADINGS`` — the per-section image filter
    already recognises the same set, so we keep the CJK vocabulary
    co-located with the only module that owns it (this file).
    """
    out: Dict[str, str] = {}
    start = find_sources_section(markdown)
    if start < 0:
        for m in _HEADING_RE.finditer(markdown):
            heading = m.group(2).strip()
            if heading.lower() in _SKIPPED_SECTION_HEADINGS:
                start = m.end()
                break
    if start < 0:
        return out
    sources_content = markdown[start:]
    for m in CITE_LIST_ROW_RE.finditer(sources_content):
        nums_str = m.group(1)
        url = (m.group(3) or "").strip()
        if not url:
            continue
        for num in nums_str.split(","):
            num = num.strip()
            if num:
                # Last write wins on duplicates; deterministic.
                out[num] = url
    return out


# Headings the per-section image filter must skip. These sections list
# citations / external references rather than substantive content —
# inserting images here is wasted work and pollutes the report with
# decorative images at the very end. Comparison is exact (after strip)
# and case-insensitive; the list covers the variants observed in
# production reports (Chinese + English).
_SKIPPED_SECTION_HEADINGS: frozenset[str] = frozenset(
    h.lower()
    for h in (
        "参考文献",
        "参考资料",
        "引用来源",
        "References",
        "Reference",
        "Sources",
        "Source",
        "Bibliography",
        "Citations",
    )
)


def is_skipped_section_heading(heading: str) -> bool:
    """True when the heading names a references/sources/citations
    section that the per-section image filter must skip.

    The check is case-insensitive and ignores leading/trailing
    whitespace. Headings with extra decoration ("## References 📚")
    are NOT matched — exact match after strip keeps the rule narrow.
    """
    if not heading:
        return False
    return heading.strip().lower() in _SKIPPED_SECTION_HEADINGS


# ---------------------------------------------------------------------------
# Section-scoped source URL extraction
# ---------------------------------------------------------------------------


def _match_terms(text: str) -> set[str]:
    """Tokenize CJK/Latin text for section-vs-search-result overlap scoring."""
    if not text:
        return set()
    out: set[str] = set()
    for span in re.findall(r"[一-鿿A-Za-z0-9]{2,}", text):
        out.add(span)
    for ch in text:
        if "一" <= ch <= "鿿":
            out.add(ch)
    return out


def _score_match(a: set[str], b: set[str]) -> int:
    if not a or not b:
        return 0
    score = len(a & b)
    return score


def extract_segment_sources(
    markdown: str, results, top_n: int = 3
) -> list[tuple[str, str, list[str]]]:
    """Map each ``##`` section to URLs cited by ``[N]`` markers in its body.

    Resolved against the trailing References list in the same document
    (see ``_scan_references_block``). Deterministic — no fuzzy token
    matching, no ratio gate. A ``[N]`` whose number appears in the
    References list with a URL is a citation; the URL flows into the
    section's allow-list.

    Sections without any ``[N]`` marker inherit the previous section's
    URL list, so an orphan section between two cited sections still
    carries the prior section's authoritative source. Returns the
    same per-section tuples as before, preserving the drift-guard
    contract with ``_split_sections`` in postprocessing.

    The ``results`` parameter is retained for API compatibility but is
    not consulted — every strategy in this repo writes
    ``"findings": []``, so the previous search-results path was dead
    code in practice. The Markdown's own references are the
    authoritative source.
    """
    del results  # not used; kept for API compatibility
    if not markdown:
        return []

    num_to_url = _scan_references_block(markdown)
    sections = _split_sections(markdown)
    offsets = _section_offsets(markdown)
    out: list[tuple[str, str, list[str]]] = []
    inherited: list[str] = []

    for idx, (heading, body) in enumerate(sections):
        body_start = offsets[idx]
        body_end = offsets[idx + 1] if idx + 1 < len(offsets) else len(markdown)
        body_slice = markdown[body_start:body_end]
        # Layer 1 dedup: set comprehension collapses [1][1][1] -> {1}
        # and [1, 2, 3] -> {"1", "2", "3"} after splitting the captured
        # comma-separated number string.
        nums: set[str] = set()
        for m in CITE_INLINE_RE.finditer(body_slice):
            nums.add(m.group(1))
        for m in CITE_INLINE_GROUP_RE.finditer(body_slice):
            for n in m.group(1).split(","):
                nums.add(n.strip())
        # Layer 2 dedup: multiple numbers mapping to the same URL are
        # collapsed so the per-section list never carries duplicates.
        urls: list[str] = []
        seen: set[str] = set()
        for n in nums:
            u = num_to_url.get(n)
            if u and u not in seen:
                urls.append(u)
                seen.add(u)
        if not urls:
            urls = list(inherited)
        out.append((heading, body, urls[:top_n]))
        inherited = urls
    return out


# ---------------------------------------------------------------------------
# Named-entity extraction
# ---------------------------------------------------------------------------

# CJK proper-name spans: runs of 2+ CJK chars that contain NO generic char.
_LATIN_PROPER_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9'-]{1,}(?:\s+[A-Z][A-Za-z0-9'-]{1,})*)\b"
)
_CJK_PROPER_RE = re.compile(r"[一-鿿]{2,}")


def filter_candidates_by_section_citations(
    candidates: list[ExtractedImage],
    section_citations: list[str],
    *,
    section_idx: int,
) -> tuple[list[ExtractedImage], int, int, int]:
    """Filter candidates to those whose source_url shares an eTLD+1
    ("pure domain") with any of this section's cited URLs.

    Combines the previous two-step pipeline (build the section's
    allowed-domain set, then test candidate eTLD+1 membership) into
    a single pass. Same-domain means same registrable domain —
    ``a1.ctrip.com`` and ``b.ctrip.com`` both reduce to ``ctrip.com``,
    ``en.wikipedia.org`` and ``zh.wikipedia.org`` both reduce to
    ``wikipedia.org``, but ``wikipedia.org`` and ``pedia.org`` do
    NOT match (the "pedia trap" — tldextract correctly distinguishes
    the registrable units).

    Behaviour mirrors the previous two-step pipeline:

    * If the section has no cited URLs (or none parseable), returns
      an empty list. The section comes out image-free.
    * Images with empty / un-parseable source_url are dropped
      (fail-closed).
    * Images whose eTLD+1 is not in the cited set are dropped
      (logged as domain_mismatch).

    Returns:
        ``(kept, dropped_no_source, dropped_domain_mismatch,
        cited_domain_count)`` — the last element is the count of
        distinct eTLD+1s the section cited, used by the caller
        for the IMG-TRACE log without re-deriving it.
    """
    cited_domains: set[str] = {
        d
        for d in (_extract_registered_domain(u) for u in section_citations or [])
        if d
    }
    kept: list[ExtractedImage] = []
    dropped_no_source = 0
    dropped_domain_mismatch = 0
    for img in candidates:
        d = _extract_registered_domain(img.source_url or "")
        if not d:
            dropped_no_source += 1
            logger.debug(
                f"[IMG-TRACE] SECTION_FILTER section={section_idx} "
                f"drop=unknown_domain url={img.url} source_url={img.source_url}"
            )
            continue
        if d not in cited_domains:
            dropped_domain_mismatch += 1
            logger.debug(
                f"[IMG-TRACE] SECTION_FILTER section={section_idx} "
                f"drop=domain_mismatch url={img.url} image_domain={d} "
                f"cited_domains={sorted(cited_domains)}"
            )
            continue
        kept.append(img)
    logger.info(
        f"[IMG-TRACE] SECTION_FILTER_SUMMARY section={section_idx} "
        f"candidates_in={len(candidates)} kept={len(kept)} "
        f"dropped={dropped_no_source + dropped_domain_mismatch} "
        f"(no_source={dropped_no_source} "
        f"domain_mismatch={dropped_domain_mismatch}) "
        f"cited_domains={sorted(cited_domains)}"
    )
    return kept, dropped_no_source, dropped_domain_mismatch, len(cited_domains)


def build_section_allowed_domains(
    per_section_citations: list[list[str]],
) -> dict[int, set[str]]:
    """Map section_idx → set of eTLD+1 domains cited by that section.

    Kept for backwards compatibility (one internal caller and a few
    tests still reference this name). The image-filter pipeline now
    uses :func:`filter_candidates_by_section_citations` directly,
    which folds the build-and-filter steps into one pass.

    A section with no cited URLs gets an empty set (NOT inherited from
    the previous section here — inheritance is the caller's concern, see
    extract_segment_sources). An empty set means "this section must
    come out image-free" under the new filter.
    """
    out: dict[int, set[str]] = {}
    for idx, urls in enumerate(per_section_citations):
        domains: set[str] = set()
        for u in urls or []:
            d = _extract_registered_domain(u)
            if d:
                domains.add(d)
        out[idx] = domains
    return out


def _candidates_for_section(
    candidates: list[ExtractedImage],
    allowed_domains: set[str],
    *,
    section_idx: int,
) -> tuple[list[ExtractedImage], int, int]:
    """Filter candidates to those whose source_url eTLD+1 is in
    ``allowed_domains``. Kept for backwards compatibility (a few tests
    reference this name). The image-filter pipeline now uses
    :func:`filter_candidates_by_section_citations` directly.

    Returns:
        ``(kept, dropped_no_source, dropped_domain_mismatch)`` —
        callers aggregate the dropped counts to emit a single
        per-research summary line at the end of the pipeline.
    """
    kept: list[ExtractedImage] = []
    dropped = 0
    dropped_no_source = 0
    dropped_domain_mismatch = 0
    for img in candidates:
        d = _extract_registered_domain(img.source_url or "")
        if not d:
            dropped += 1
            dropped_no_source += 1
            logger.debug(
                f"[IMG-TRACE] SECTION_FILTER section={section_idx} "
                f"drop=unknown_domain url={img.url} source_url={img.source_url}"
            )
            continue
        if d not in allowed_domains:
            dropped += 1
            dropped_domain_mismatch += 1
            logger.debug(
                f"[IMG-TRACE] SECTION_FILTER section={section_idx} "
                f"drop=domain_mismatch url={img.url} image_domain={d} "
                f"allowed={sorted(allowed_domains)}"
            )
            continue
        kept.append(img)
    logger.info(
        f"[IMG-TRACE] SECTION_FILTER_SUMMARY section={section_idx} "
        f"candidates_in={len(candidates)} kept={len(kept)} "
        f"dropped={dropped} (no_source={dropped_no_source} "
        f"domain_mismatch={dropped_domain_mismatch}) "
        f"allowed_domains={sorted(allowed_domains)}"
    )
    return kept, dropped_no_source, dropped_domain_mismatch


def _candidate_spans(text: str) -> Iterable[str]:
    """Yield candidate proper-name spans from text (no generic filtering yet).

    For CJK we emit:
    - each contiguous CJK run as a whole
    - 2- and 3-character prefixes/suffixes anchored at the run boundary
      (so `中山纪念堂` also yields `中山` and `纪念堂`)
    - 2-character interior spans only if they are NOT composed entirely
      of generic tokens (most single common-word 2-char substrings like
      `州塔`, `塔位` get filtered out by the generic check downstream).

    Latin yields title-case multi-word phrases.
    """
    if not text:
        return ()
    seen: set[str] = set()
    for m in _LATIN_PROPER_RE.finditer(text):
        s = m.group(1)
        if s and s not in seen:
            seen.add(s)
            yield s
    for m in re.finditer(r"[一-鿿]+", text):
        run = m.group(0)
        if run and run not in seen:
            seen.add(run)
            yield run
        # Anchored sub-spans (start- or end-of-run): these are the most
        # common proper-name forms (location prefixes, suffixes).
        for n in (2, 3):
            if len(run) >= n:
                pref = run[:n]
                suff = run[-n:]
                if pref not in seen:
                    seen.add(pref)
                    yield pref
                if suff not in seen and suff != pref:
                    seen.add(suff)
                    yield suff


def _extract_named_entities(text: str) -> FrozenSet[str]:
    """Return the normalized named entities found in `text`.

    A token/spans qualifies as a named entity only if it has at least one
    character NOT in the generic vocabulary. Spans are returned as exact
    substring matches (no further normalization beyond stripping).
    """
    if not text:
        return frozenset()
    out: set[str] = set()
    for span in _candidate_spans(text):
        toks = _tokens(span)
        if not toks:
            continue
        if all(t in _GENERIC_TOKENS for t in toks):
            continue
        # Must have at least one non-generic token to count as a proper
        # name. We additionally require the span be at least 2 chars.
        if len(span) >= 2:
            out.add(span)
    return frozenset(out)


def _extract_alt_entities(alt: str, context_entities: FrozenSet[str] = frozenset()) -> FrozenSet[str]:
    """Extract named entities from an alt string.

    Alts are short. We look for:
    1. Full proper-name spans in the alt (CJK runs / Latin Title-Case runs).
    2. Suffix variants after stripping a leading generic token (e.g.
       `广州塔珠江夜景` → `广州塔珠江夜`, `广州塔珠江`, etc.).
    3. Any context entity that appears as a substring of the alt, since
       alts like `广州塔珠江夜景` only contain a subset of the report's
       named entities as contiguous spans.

    We then suppress short interior sub-spans that are proper
    substrings of a longer entity also yielded, to avoid tagging
    descriptive padding (e.g. `江夜景`) as foreign proper names.
    """
    if not alt:
        return frozenset()
    raw: set[str] = set()
    # 1. Pure extraction (no context knowledge).
    for e in _extract_named_entities(alt):
        raw.add(e)
    # 2. Suffix-stripping: drop leading generic tokens to surface embedded
    # proper names (e.g. `广州塔珠江夜景` → `广州塔珠江夜`, `广州塔珠江`, etc.).
    toks = _tokens(alt)
    for i in range(1, len(toks)):
        if toks[i - 1] in _GENERIC_TOKENS:
            suffix = "".join(toks[i:])
            for e in _extract_named_entities(suffix):
                raw.add(e)
    # 3. Substring matching against context entities. This catches alts
    # like `广州塔珠江夜景` where `广州塔` is a context entity embedded
    # inside a longer compound alt.
    for ce in context_entities:
        if not ce:
            continue
        if ce in alt and ce not in raw:
            raw.add(ce)

    # Suppress interior sub-spans. If a longer entity L is also in `raw`
    # and a shorter entity S is a proper substring of L, drop S —
    # descriptive padding like `江夜景` shouldn't be treated as a
    # proper name when `珠江夜景` (its parent) is also a candidate.
    sorted_by_len = sorted(raw, key=lambda s: (-len(s), s))
    keep: set[str] = set()
    for cand in sorted_by_len:
        if any(
            other != cand and cand in other and len(other) > len(cand)
            for other in sorted_by_len
        ):
            continue
        keep.add(cand)
    return frozenset(keep)


# ---------------------------------------------------------------------------
# Context & decision dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportEntityContext:
    primary_entities: FrozenSet[str]
    section_entities: Tuple[FrozenSet[str], ...]
    all_entities: FrozenSet[str]
    entity_relations: FrozenSet[Tuple[str, str, str]]
    section_sources: Tuple[Tuple[str, str, Tuple[str, ...]], ...]
    query: str


@dataclass(frozen=True)
class ImageRelevanceDecision:
    url: str
    status: Literal["keep", "drop"]
    reason: str
    entities: FrozenSet[str]
    matched_sections: FrozenSet[int]
    source_signal: Literal["strong", "weak", "none"]
    evidence_refs: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


class ContextBuildFailed(Exception):
    """Raised when the report entity context cannot be safely built."""


def _iter_finding_text(results: Dict[str, Any]) -> Iterable[str]:
    if not isinstance(results, dict):
        return
    for finding in results.get("findings", []) or []:
        if not isinstance(finding, dict):
            continue
        for sr in finding.get("search_results", []) or []:
            if not isinstance(sr, dict):
                continue
            for key in ("title", "content", "snippet", "url", "link", "source_title"):
                val = sr.get(key)
                if isinstance(val, str) and val:
                    yield val


def build_report_entity_context(
    clean_markdown: str,
    results: Dict[str, Any],
    query: str = "",
) -> ReportEntityContext:
    """Build the report-entity context from current-run text only."""
    if not isinstance(clean_markdown, str):
        raise ContextBuildFailed("clean_markdown not a string")
    try:
        sections = _split_sections(clean_markdown)
    except Exception as exc:
        raise ContextBuildFailed(f"section split failed: {exc}")

    # Collect entities per section. Index 0 is the first heading's
    # section (which may include the report's pre-heading prose).
    section_entities_list: list[FrozenSet[str]] = []
    primary: set[str] = set()
    all_entities: set[str] = set()
    for heading, body in sections:
        text = (heading + "\n" + body) if heading else body
        ents = _extract_named_entities(text)
        section_entities_list.append(ents)
        all_entities.update(ents)
        if heading:
            primary.update(ents)

    # Pull entities from the query and search-result metadata.
    if query:
        primary.update(_extract_named_entities(query))
        all_entities.update(_extract_named_entities(query))
    for txt in _iter_finding_text(results or {}):
        ents = _extract_named_entities(txt)
        primary.update(ents)
        all_entities.update(ents)

    # Build entity relations from the report text AND the search-result
    # metadata (not external knowledge).
    relations: set[Tuple[str, str, str]] = set()

    def _collect_relations(text: str) -> None:
        for subj, loc in _find_loc_relations(text):
            if (
                any(_entity_link(subj, e) for e in all_entities)
                and any(_entity_link(loc, e) for e in all_entities)
            ):
                relations.add(("located_in", subj, loc))
        for m in _DE_PHRASE_RE.finditer(text):
            owner = m.group(1)
            target = m.group(2)
            if (
                any(_entity_link(owner, e) for e in all_entities)
                and any(_entity_link(target, e) for e in all_entities)
            ):
                relations.add(("possessive", owner, target))

    for heading, body in sections:
        text = (heading + "\n" + body) if heading else body
        _collect_relations(text)
    for txt in _iter_finding_text(results or {}):
        _collect_relations(txt)

    # Also derive section sources for the mapping-miss rescue heuristic.
    section_sources: list[Tuple[str, str, Tuple[str, ...]]] = []
    if isinstance(results, dict):
        for finding in results.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            for sr in finding.get("search_results", []) or []:
                if not isinstance(sr, dict):
                    continue
                title = sr.get("title") or sr.get("source_title") or ""
                url = sr.get("url") or sr.get("link") or ""
                if title and url:
                    section_sources.append((title, url, (title,)))

    return ReportEntityContext(
        primary_entities=frozenset(primary),
        section_entities=tuple(section_entities_list),
        all_entities=frozenset(all_entities),
        entity_relations=frozenset(relations),
        section_sources=tuple(section_sources),
        query=query or "",
    )


# ---------------------------------------------------------------------------
# Candidate evaluation
# ---------------------------------------------------------------------------

_WEAK_SOURCE_HOSTS: tuple[str, ...] = (
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "twitter.com",
    "x.com",
    "t.me",
    "telegram.org",
    "facebook.com",
)


def _source_signal(source_url: str) -> Literal["strong", "weak", "none"]:
    if not source_url:
        return "none"
    try:
        host = (urlparse(source_url).hostname or "").lower()
    except Exception:
        return "none"
    if not host:
        return "none"
    if host in _WEAK_SOURCE_HOSTS:
        return "weak"
    if any(host.endswith("." + h) for h in _WEAK_SOURCE_HOSTS):
        return "weak"
    return "strong"


def evaluate_candidate(
    candidate: ExtractedImage, context: ReportEntityContext
) -> ImageRelevanceDecision:
    """Decide whether a candidate image should be kept or dropped.

    Order:
    1. missing_alt → drop
    2. compound generic → drop(no_named_entity)
    3. no extracted entities → drop(no_named_entity)
    4. any alt entity NOT in report context → drop(foreign_entity_conflict)
    5. no section/entity-relation match → drop(unresolved_entity_relation)
    6. keep — context_match if source mapped, else context_entity_rescue
    """
    src_signal = _source_signal(candidate.source_url)

    def _drop(reason: str, evidence: tuple[str, ...] = (), ents=frozenset()):
        return ImageRelevanceDecision(
            url=candidate.url,
            status="drop",
            reason=reason,
            entities=ents,
            matched_sections=frozenset(),
            source_signal=src_signal,
            evidence_refs=evidence,
        )

    if not candidate.alt or not candidate.alt.strip():
        return _drop("missing_alt")

    # Step 1: compound generic short-circuit.
    if _is_compound_generic(candidate.alt):
        return _drop("no_named_entity", evidence=(candidate.alt,))

    entities = _extract_alt_entities(candidate.alt, context.all_entities)
    if not entities:
        return _drop("no_named_entity", evidence=(candidate.alt,))

    # Step 2: foreign-entity conflict detection.
    # An alt entity is "anchored" to the report context if it is
    # contained in, or contains, a context entity. When the alt has
    # NO anchored entities, the entire alt is foreign — drop with
    # foreign_entity_conflict. When some entities are un-anchored AND
    # those un-anchored entities are real proper names (i.e. have
    # non-generic tokens, not just descriptive generic padding), drop
    # as foreign_entity_conflict. When un-anchored entities are
    # generic descriptive padding (e.g. `夜景`, `江夜景` in
    # `广州塔珠江夜景`), ignore them and proceed with anchored-only
    # matching.
    def _anchored(ent: str) -> bool:
        # Require a *substantial* entity as the anchor: either a CJK
        # proper-name span (>=3 chars), or an English/Latin span of
        # at least 5 letters/digits. Short English tokens such as
        # "Asia", "LZW", "March", "Photo" are not substantial — they
        # match too loosely and produce false-positive anchors (e.g.
        # a candidate alt of `Photo by LZW on March 21, 2015.` would
        # otherwise be anchored against any report that happens to
        # mention `Asia`).
        def _is_substantial(span: str) -> bool:
            if not span:
                return False
            if re.search(r"[一-鿿]", span):
                return len(span) >= 3
            return len(span) >= 5

        if ent in context.all_entities:
            return _is_substantial(ent)

        for ce in context.all_entities:
            if not _is_substantial(ce):
                continue
            if ce in ent or ent in ce:
                return True
        return False

    def _is_real_proper_name(ent: str) -> bool:
        """True if `ent` looks like a real proper name (not generic padding).

        We treat 2-char spans as padding unless they are themselves a
        known entity in the report context — real proper names in this
        domain are typically 3+ CJK characters (广州, 重庆, 中山 are
        short, but they appear as parts of longer phrases). The brief
        specifically calls out `重庆` as a foreign entity, so we DO
        consider 2-char non-anchored spans as real when they survive
        the compound-generic pre-filter — but we require the alt to
        also have NO anchored entities in that case. Mixed cases
        (some anchored, some 2-char unanchored) are treated as
        descriptive padding.
        """
        if any(ent.startswith(p) for p in _VAGUE_QUALIFIER_PREFIXES):
            return False
        toks = _tokens(ent)
        if not toks:
            return False
        non_generic = [t for t in toks if t not in _GENERIC_TOKENS]
        if not non_generic:
            return False
        # 3+ char spans with non-generic content are real proper names.
        if len(ent) >= 3:
            return True
        # 2-char spans are treated as padding unless the alt has no
        # anchored entities at all (handled by the caller).
        return False

    anchored_entities = frozenset(e for e in entities if _anchored(e))
    foreign_entities = [
        e for e in entities
        if not _anchored(e) and _is_real_proper_name(e)
    ]
    if context.all_entities and foreign_entities:
        return _drop(
            "foreign_entity_conflict",
            evidence=tuple(sorted(foreign_entities)),
            ents=entities,
        )

    # Expand anchored_entities to include context entities that are
    # substrings of any anchored alt entity, so downstream matching
    # can correlate `广州塔珠江夜景` → `广州塔` → section 1.
    expanded: set[str] = set(anchored_entities)
    for ce in context.all_entities:
        if not ce:
            continue
        if any(ce in ae or ae in ce for ae in anchored_entities):
            expanded.add(ce)
    entities = frozenset(expanded)

    # Step 3: match entities against sections.
    matched: set[int] = set()
    for idx, sect_ents in enumerate(context.section_entities):
        if sect_ents & entities:
            matched.add(idx)

    # Step 4: explicit report-level relation (e.g. `X位于Y` found in text).
    explicit_report_relation = any(
        rel[1] in entities or rel[2] in entities
        for rel in context.entity_relations
    )

    if not matched and not explicit_report_relation:
        return _drop(
            "unresolved_entity_relation",
            evidence=tuple(sorted(entities)),
            ents=entities,
        )

    # Step 5: source URL must be one of the section sources actually cited
    # in the Markdown. Without this constraint, an alt that happens to
    # mention a context entity could pass the gate even when its page is
    # unrelated to anything the report cites (e.g. an Instagram photo
    # that name-drops a Guangzhou landmark). We require exact match
    # against any section source URL.
    norm_source = _normalize_url(candidate.source_url)
    source_mapped = False
    if norm_source:
        for _title, url, _titles in context.section_sources:
            if _normalize_url(url) == norm_source:
                source_mapped = True
                break
    if not source_mapped:
        return _drop(
            "drop_source_url_not_cited",
            evidence=(candidate.source_url or "",),
            ents=entities,
        )
    keep_reason = "context_match"

    return ImageRelevanceDecision(
        url=candidate.url,
        status="keep",
        reason=keep_reason,
        entities=entities,
        matched_sections=frozenset(matched),
        source_signal=src_signal,
        evidence_refs=tuple(sorted(entities)),
    )
