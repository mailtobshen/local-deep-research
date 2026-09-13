"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from .bank import ImageBank
from .reference_sanitizer import sanitize_references
from .relevance import (
    _find_parent_heading,
    _section_levels,
    _split_sections,
    build_citation_index,
    build_url_text_index,
    domains_match,
)
from .semantic_matcher import (
    DEFAULT_MIN_MARGIN as _DEFAULT_MIN_MARGIN,
    DEFAULT_THRESHOLD as _DEFAULT_THRESHOLD,
    _cosine,
    _encode_phrase_cached,
    round_score,
)
# Import the module for direct function access (needed for monkeypatching in tests)
from . import semantic_matcher
from .serialize import loads_images
from .store import ImageStore, _IMG_RE
from .extractor import _MIN_DIM, pop_channel_coverage
from local_deep_research.utilities.is_darkweb_url import is_darkweb_url

# Meaningless-alt patterns for the darkweb adoption fast path. Keep
# this STRICT (drop only truly content-free alts): per the 2026-08-22
# policy decision, dimension-style alts ('300x300', 'image 300x213')
# and ambiguous short tokens ('shop', 'bookc') may belong to real
# content images — the place to kill small/icon images is the SIZE
# filter (which now also applies to dimension-hinted alts), not the
# alt text. Only pure UI/navigation vocabulary is dropped here.
_MEANINGLESS_ALT_RE = re.compile(
    r"^(?:placeholder|home|logo|banner|button|arrow|search|menu|next|previous|prev|back|close|avatar)$"
    # 2026-08-26 (research 9b514fa0, AK47): 'Partner Banner' — the
    # Kalashnikov site's 2340x300 ad banners — escaped the exact-match
    # rule above 51 times and 3 shipped into the report. Banner-ness is
    # vocabulary-driven (same substring policy as 'icon'/'shop' below):
    # word-boundary so 'bannerman' style prose still passes.
    r"|\bbanner\b"
    r"|^thumbnail(\s+for)?\s*$"
    r"|^photo$|^picture$|^img$"
    # WooCommerce placeholder pattern (2026-08-23, research 3e9ee493):
    # 'Awaiting product image' is the theme's not-yet-uploaded stub —
    # same semantic as 'placeholder', adopted by mistake when it was
    # the only image on a product page.
    r"|awaiting\s+(product\s+)?image"
    # Language-selector artifacts (2026-08-23): the same flag icon is
    # titled 'Language selector icon' on one page and bare 'Lang' on
    # another — 'icon' catches the first, this anchors the second.
    r"|^lang$|\blang(?:uage)?\s+selector\b"
    # Substring rules (2026-08-22 policy): alts CONTAINING 'icon' or
    # 'shop' are theme/asset artifacts ('shop', 'shop icon',
    # 'Language selector icon') regardless of what else they say.
    r"|icon"
    r"|^shop$|\bshop\b"
    # Site-promo / commercial-ad vocabulary (2026-08-23 policy): the
    # Monero-site batch — 'Create wallet', 'Exchange', 'Merchants',
    # 'Contribute', 'FAQ', 'onion service' — is pure service-page
    # advertising, not research content. Word-boundary anchored so
    # e.g. 'wallets recovery phrase' style prose alts still pass.
    r"|\bcreate\s+wallet\b"
    r"|\bexchange\b"
    r"|\bmerchants?\b"
    r"|\bcontribute\b"
    r"|\bfaq\b"
    r"|\bonion\s+service\b"
    r"|\bget\s+started\b"
    r"|\bsign\s+up\b|\bregister\b|\blogin\b|\blog\s+in\b"
    r"|\bdownload\b|\binstall\b"
    r"|\bdonate\b"
    r"|\bpricing\b|\bsubscribe\b|\bnewsletter\b",
    re.IGNORECASE,
)

# Alt strings that embed the image's pixel dimensions, e.g. '300x300'
# or 'image 740x555'. Not dropped (they can be real content images),
# but the embedded size is authoritative when the width/height
# attributes are missing — a dimension-hinted alt below the icon
# threshold is a logo/badge and IS dropped by the size rule.
_DIM_IN_ALT_RE = re.compile(r"(\d+)\s*[x×]\s*(\d+)")

# 2026-08-23 policy: on darkweb runs, images whose filename OR alt
# references a messaging platform or an email address are contact-
# channel evidence (vendor QQ/WeChat/Telegram cards, burner-email
# screenshots) — core intelligence for darkweb research. They are
# force-adopted: the meaningless-alt and size filters do not apply,
# and the placement tie-break puts them at the very front.
_MESSAGING_HINT_RE = re.compile(
    r"qq|weixin|wechat|微信|\bq q\b|telegram|电报|tg[ _:-]"
    r"|whatsapp|facebook|fb[ _:-]|messenger|linkedin|line[ _:-]"
    r"|kakaotalk|kakao|viber|vibe[ _:-]|imessage|snapchat|discord"
    r"|signal[ _:-]|email|e-mail|mailbox|邮件|邮箱|gmail|outlook"
    r"|protonmail|@[a-z0-9.-]+\.(com|net|org|onion|ru|io|me)",
    re.IGNORECASE,
)


def _is_messaging_evidence(img) -> bool:
    """True when the image's alt or filename references a messaging
    platform or email address (darkweb contact-channel evidence)."""
    alt = (getattr(img, "alt", "") or "")
    if _MESSAGING_HINT_RE.search(alt):
        return True
    name = (getattr(img, "url", "") or "").rsplit("/", 1)[-1]
    return bool(_MESSAGING_HINT_RE.search(name))


def _dims_from_alt(alt: str) -> tuple[int, int] | None:
    """Extract (w, h) from a dimension-style alt like '300x300'.

    Returns None when the alt carries no embedded dimensions.
    """
    m = _DIM_IN_ALT_RE.search(alt or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _alt_is_meaningless(alt: str) -> bool:
    """True when the alt carries no descriptive value.

    Uses ``search`` (not ``match``) because the trailing alternation
    arms are substring rules ('icon', 'shop') that must hit anywhere
    in the alt; the ``^…$``-anchored arms are unaffected by search.
    """
    return bool(_MEANINGLESS_ALT_RE.search((alt or "").strip()))


def _log_end(research_id: str, status: str) -> None:
    """Emit END plus the run's alt-channel coverage rollup.

    Coverage is drained here (and on every exit path) so the extractor's
    per-research tallies never accumulate in a long-lived process.
    ``*_pages`` counts pages that actually reached extraction, which is
    what distinguishes "channel ran and declined" from "channel never
    ran" — a domain-gated fallback whose pages all failed to fetch shows
    zero hits either way.
    """
    cov = pop_channel_coverage(research_id) or {}
    logger.info(f"[IMG-TRACE] END research={research_id} status={status}")
    pages = cov.get("pages", 0)
    logger.info(
        f"[IMG-TRACE] ALT_CHANNEL_COVERAGE research={research_id} "
        f"pages={pages} "
        f"wiki_pages={cov.get('wiki_pages', 0)} "
        f"baike_pages={cov.get('baike_pages', 0)} "
        f"other_pages={pages - cov.get('wiki_pages', 0) - cov.get('baike_pages', 0)} "
        f"images={cov.get('images', 0)} "
        f"had_alt={cov.get('had_alt', 0)} "
        f"wiki_hits={cov.get('wiki_hits', 0)} "
        f"baike_hits={cov.get('baike_hits', 0)} "
        f"filename_hits={cov.get('filename_hits', 0)} "
        f"unresolved={cov.get('unresolved', 0)}"
    )


SECTION_IMAGE_CAP = 3  # max images adopted per section, by score (clearnet)

# --- alt semantic-content gate (2026-09-13, research c34cb8fa) ---
# The multilingual encoder embeds filename-derived token soup
# ('ldgjhfknbaicem1496932883', Baidu CDN hashes) near Chinese section
# phrases (cosine 0.61-0.65, verified offline 2026-09-13), so such alts
# must never reach the encoder. An alt participates only if it carries
# real lexical content: >=2 CJK chars, or >=1 latin word (>=3 letters)
# that is neither an ID/timestamp (>=6-digit run attached to letters)
# nor consonant soup (>=10 letters with <25% vowels).
_ALT_CJK_RE = re.compile(r"[一-鿿]{2,}")
_ALT_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_ALT_DIGIT_RUN_RE = re.compile(r"\d{6,}")


def _is_junk_latin_token(token: str) -> bool:
    if _ALT_DIGIT_RUN_RE.search(token):
        return True
    letters = [c for c in token if c.isalpha()]
    if len(letters) >= 10:
        vowels = sum(c in "aeiouAEIOU" for c in letters)
        if vowels / len(letters) < 0.25:
            return True
    return False


def _alt_has_semantic_content(alt: Optional[str]) -> bool:
    if not alt or not alt.strip():
        return False
    if _ALT_CJK_RE.search(alt):
        return True
    return any(
        not _is_junk_latin_token(w) for w in _ALT_WORD_RE.findall(alt)
    )


# Spread-pass threshold relaxation (e14f3600, 2026-08-30): an over-cap
# image may re-seat into another SAME-CITE section when its alt-vs-
# section cosine clears (main threshold − this margin). 0.15 keeps
# the "some similarity" bar meaningfully above zero while admitting
# heading/parent overlap the strict gate rejected.
_SPREAD_THRESHOLD_RELAX = 0.15
SECTION_IMAGE_CAP_DARKWEB = 5  # darkweb runs adopt more per section

# Sorting rank for the tie-break chain (lower = wins). A meaningful
# alt/file label is one that carries real vocabulary — not a bare
# dimension string, not the UI-word blacklist.
_CONTENT_HINT_RE = re.compile(r"[一-鿿]{2,}|[a-z]{4,}", re.IGNORECASE)


def _substance_rank(img) -> int:
    """0 = descriptive alt, 1 = descriptive filename, 2 = neither.

    An alt that the UI-vocabulary blacklist (icon/shop/home/…) or a
    bare dimension string matches is NOT descriptive, even when it
    has ≥4 letters ('shop' passes the naive length check).
    """
    alt = (getattr(img, "alt", "") or "").strip()
    if (
        alt
        and not _MEANINGLESS_ALT_RE.search(alt)
        and not _DIM_IN_ALT_RE.fullmatch(alt)
        and _CONTENT_HINT_RE.search(alt)
    ):
        return 0
    name = (getattr(img, "url", "") or "").rsplit("/", 1)[-1]
    name = name.rsplit(".", 1)[0]
    if _CONTENT_HINT_RE.search(name) and not _MEANINGLESS_ALT_RE.search(name):
        return 1
    return 2


# Default area for images with NO dimension information at all (attrs
# missing, alt carries no embedded dims): assumed 300x300 (2026-08-23
# policy). Keeps unknown-size images competitive against small known
# icons (a 100x100=10k thumbnail ranks BELOW an unknown-size image)
# while real large images (740x555≈410k) still outrank them.
_UNKNOWN_AREA_DEFAULT = 300 * 300


def _area(img) -> int:
    """Best-known pixel area: attrs first, alt-embedded dims fallback,
    300x300 default when both are absent."""
    w, h = getattr(img, "width", None), getattr(img, "height", None)
    if (w is None or h is None) and getattr(img, "alt", ""):
        d = _dims_from_alt(img.alt)
        if d:
            w = w if w is not None else d[0]
            h = h if h is not None else d[1]
    if w is None or h is None:
        return _UNKNOWN_AREA_DEFAULT
    return w * h


def _build_placements(
    binding: dict,
    bank_by_url: dict,
    cap: int = SECTION_IMAGE_CAP,
    _caption_fallback: bool = False,
    darkweb: bool = False,
    spread: bool = True,
    num_to_url: Optional[dict] = None,
    section_to_nums: Optional[dict] = None,
    section_vecs: Optional[dict] = None,
    section_phrases: Optional[dict] = None,
    alt_vecs: Optional[dict] = None,
    spread_relaxed_threshold: float = 0.0,
) -> list[tuple[int, str, str]]:
    """Build (sidx, url, alt) placements, capped per section.

    Cap: ``SECTION_IMAGE_CAP_DARKWEB`` (5) when the run is darkweb-
    engine-driven, else ``SECTION_IMAGE_CAP`` (3). 2026-08-23 policy.

    Within a section the selection order is:
      1. binding score desc (semantic-gate runs only — darkweb
         fast-path images all score 0.0 and tie),
      2. tie-break chain: image AREA desc (bigger = likelier the
         page's main content image), then substance rank (descriptive
         alt > descriptive filename > dimension-only/empty),
      3. stable insertion order (URL bind order) as final tiebreak.

    Emits a SECTION_CAP IMG-TRACE per over-cap section. When
    ``_caption_fallback`` is set, an empty alt is replaced by the
    source page's title so the image still renders with a caption.
    """
    eff_cap = cap if not darkweb else SECTION_IMAGE_CAP_DARKWEB
    if spread and not darkweb:
        # e14f3600 spread policy: at most ONE seated image per section
        # on clearnet — everything else the gate adopted becomes the
        # spread pool that the pass below redistributes into image-
        # less same-cite sections. (The caller passes cap=SECTION_
        # IMAGE_CAP for API compat; the spread mode overrides it.)
        eff_cap = 1

    def _caption_for(img) -> str:
        alt = (img.alt or "").strip()
        if alt:
            return alt
        if _caption_fallback:
            t = (getattr(img, "source_title", "") or "").strip()
            if t:
                return t[:80]
        return ""

    def _sort_key(entry: tuple[float, str]):
        score, url = entry
        img = bank_by_url[url]
        # Messaging-evidence images (darkweb contact-channel intel)
        # outrank everything, ahead of the score itself — they are the
        # reason the rule exists (2026-08-23 policy).
        return (
            0 if _is_messaging_evidence(img) else 1,
            -score,
            -_area(img),
            _substance_rank(img),
            url,
        )

    # Gather per-section candidates with scores.
    by_sec: dict[int, list[tuple[float, str]]] = {}
    for url, pairs in binding.items():
        if url not in bank_by_url:
            continue
        for _num, sidx, score in pairs:
            by_sec.setdefault(sidx, []).append((score, url))
    placements: list[tuple[int, str, str]] = []
    seated: set[str] = set()  # one placement per image URL overall
    for sidx, cands in by_sec.items():
        cands_sorted = sorted(cands, key=_sort_key)
        dropped = max(0, len(cands_sorted) - eff_cap)
        if dropped:
            logger.info(
                f"[IMG-TRACE] SECTION_CAP sec={sidx} "
                f"cap={eff_cap} darkweb={darkweb} "
                f"candidates={len(cands_sorted)} kept={eff_cap} "
                f"dropped={dropped}"
            )
        placed_this_sec: list[str] = []
        for _score, url in cands_sorted:
            # Per-section cap counts SEATED images; multi-bind URLs
            # bound to several sections are seated ONCE overall (first
            # section by binding order), later sections skip them and
            # the next candidate takes the slot. Under the e14f3600
            # spread policy the seat count is what the spread pass
            # redistributes — a URL placed twice would be collapsed by
            # _dedupe_images anyway, leaving a phantom slot.
            if url in seated:
                continue
            if len(placed_this_sec) >= eff_cap:
                break
            placements.append((sidx, url, _caption_for(bank_by_url[url])))
            seated.add(url)
            placed_this_sec.append(url)
    placements.sort(key=lambda p: (p[0], p[1]))

    # --- Spread pass (research e14f3600, 2026-08-30) ---
    # Observed: all adopted images clustered in a handful of sections
    # (sec 15/22/34/36/43/50 carried 3 each from the same 2 cites)
    # while dozens of image-less sections cited the SAME URLs. Policy:
    #   S1  one image per section max (per-section cap above drops
    #       the over-cap images → they enter the spread pool instead);
    #   S2  an over-cap image may be re-seated into ANOTHER section
    #       that cites the SAME cite_num (its provenance is intact:
    #       the section genuinely references the image's source page)
    #       and whose alt-vs-section cosine clears a RELAXED threshold
    #       (below the strict adoption gate — "some similarity" per
    #       the spread policy);
    #   S3  earliest matching section wins (front sections have
    #       spread priority), each section still receives ≤1 spread
    #       image, and an image is never placed twice (the downstream
    #       _dedupe_images pass is the final backstop).
    if not (spread and num_to_url and section_to_nums and section_vecs):
        return placements

    def _sec_cites(sec: int) -> set:
        return set(section_to_nums.get(sec) or ())

    placed_by_sec: dict[int, list[str]] = {}
    placed_urls: set[str] = set()
    for sidx, url, _alt in placements:
        placed_by_sec.setdefault(sidx, []).append(url)
        placed_urls.add(url)

    # url -> the cite_num(s) it was adopted under (provenance for
    # matching candidate sections).
    url_cites: dict[str, set] = {}
    for url, pairs in binding.items():
        url_cites.setdefault(url, set()).update(p[0] for p in pairs)

    def _alt_vec(url):
        if alt_vecs is not None:
            return alt_vecs.get(url)
        return None

    spread_moves: list[tuple[int, str, str, int, float]] = []
    # Fallback ledger (user rule, 2026-08-30): when the minimum
    # spread conditions are not met, the adopted image KEEPS its
    # original bind position — spread is an optimization, never a
    # content-loss path.
    spread_fallbacks: list[tuple[int, str, str]] = []
    # Sections already carrying ≥1 placement are ineligible targets.
    for sidx, cands in sorted(by_sec.items()):
        cands_sorted = sorted(cands, key=_sort_key)
        overflow = cands_sorted[eff_cap:]
        if not overflow:
            continue
        for _score, url in overflow:
            if url in placed_urls:
                # Already seated somewhere — the dedup pass keeps only
                # its first occurrence anyway; don't spread a dupe.
                continue
            img = bank_by_url.get(url)
            if img is None or not (img.alt and img.alt.strip()):
                # Not spread-eligible AND never seated: the adoption
                # decision stands — keep the image at its ORIGINAL
                # bind position rather than dropping it (fallback
                # rule, see below).
                placements.append((sidx, url, _caption_for(img)))
                placed_by_sec.setdefault(sidx, []).append(url)
                placed_urls.add(url)
                spread_fallbacks.append((sidx, url, "no_alt_or_missing"))
                continue
            vec = _alt_vec(url)
            cites = url_cites.get(url) or set()
            # Candidate sections: cite the same number, have a
            # section vector, no placement yet — ascending (front
            # priority).
            targets = sorted(
                sec
                for sec, sc_vec in section_vecs.items()
                if sec != sidx
                and not placed_by_sec.get(sec)
                and _sec_cites(sec) & cites
                and sc_vec is not None
            )
            spread_target = None
            if vec is not None:
                for t_sec in targets:
                    t_vec = section_vecs.get(t_sec)
                    if t_vec is None:
                        continue
                    sim = _cosine(vec, t_vec)
                    if round_score(sim) >= round_score(spread_relaxed_threshold):
                        spread_target = (t_sec, round_score(sim))
                        break
            if spread_target is not None:
                t_sec, sim = spread_target
                alt_txt = _caption_for(img)
                placements.append((t_sec, url, alt_txt))
                placed_by_sec.setdefault(t_sec, []).append(url)
                placed_urls.add(url)
                spread_moves.append((sidx, url, alt_txt, t_sec, sim))
                logger.info(
                    f"[IMG-TRACE] SPREAD_MOVE url={url} "
                    f"from_sec={sidx} to_sec={t_sec} "
                    f"sec_phrase=\"{(section_phrases or {}).get(t_sec, '')[:80]}\" "
                    f"cite_nums={sorted(cites)} sim={sim:.2f} "
                    f"threshold={round_score(spread_relaxed_threshold):.2f}"
                )
            else:
                # Minimum spread conditions NOT met (no same-cite
                # image-less target above the relaxed threshold, or no
                # alt vector available): the image was ADOPTED by the
                # gate — keep its ORIGINAL insertion position in the
                # home section instead of dropping it. The 1-per-
                # section ideal yields to not losing adopted content.
                placements.append((sidx, url, _caption_for(img)))
                placed_by_sec.setdefault(sidx, []).append(url)
                placed_urls.add(url)
                reason = (
                    "no_alt_vec" if vec is None else "no_qualified_target"
                )
                spread_fallbacks.append((sidx, url, reason))
                logger.info(
                    f"[IMG-TRACE] SPREAD_FALLBACK url={url} "
                    f"sec={sidx} reason={reason} "
                    f"candidates={len(targets)}"
                )
    if spread_moves:
        logger.info(
            f"[IMG-TRACE] SPREAD_SUMMARY moves={len(spread_moves)} "
            f"from_secs={sorted({m[0] for m in spread_moves})} "
            f"to_secs={sorted({m[3] for m in spread_moves})} "
            f"fallbacks={len(spread_fallbacks)}"
        )
    elif spread_fallbacks:
        logger.info(
            f"[IMG-TRACE] SPREAD_SUMMARY moves=0 "
            f"fallbacks={len(spread_fallbacks)} "
            f"fallback_secs={sorted({f[0] for f in spread_fallbacks})}"
        )
    placements.sort(key=lambda p: (p[0], p[1]))
    return placements


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
                f"[IMG-TRACE] DEDUPE_DROP alt={alt!r} "
                f"img_url={url}"
            )
        else:
            seen.add(url)
            parts.append(m.group(0))
            logger.info(
                f"[IMG-TRACE] DEDUPE_KEEP alt={alt!r} "
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
    fetched_html: Optional[Dict[str, str]] = None,
    research_query: Optional[str] = None,
) -> str:
    """Return markdown with real images inserted + mirrored locally.

    When enable_images is False, returns clean_markdown unchanged.

    ``fetched_html`` (2026-08-29 reorder) is the direct payload channel
    from ``_deferred_image_fill``: ``{url: serialized_images}``. When
    present it takes precedence over the legacy
    ``results["findings"][].search_results[].html_content`` reads in
    build_citation_index, decoupling image placement from the search
    -results bookkeeping entirely.
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

        # Legacy channel bridging: when the caller passes the direct
        # fetched_html channel, publish it onto results so
        # build_citation_index's url_to_html assembly (findings +
        # all_links reads) picks the payloads up through the same
        # "_image_fetch_html" key the deferred fill writes.
        if fetched_html is not None:
            results["_image_fetch_html"] = fetched_html

        # Stage 0: build citation index from the cleaned markdown + results.
        num_to_url, section_to_nums, url_to_html = build_citation_index(
            clean_markdown, results
        )
        # Event 1 (closes G2): SEC_CITE_INDEX — per-section cite-num trace.
        # Aug 6 log lacked per-(sec, nums) visibility; this emits one
        # line per sec that has ≥1 cite_num in its body [[N]] tokens.
        # section_phrases is built below (line ~252); emit lazily after
        # it's populated. We register a placeholder here so the event
        # ordering is preserved.
        def _emit_sec_cite_index():
            for sidx, nums in section_to_nums.items():
                if not nums:
                    continue
                sec_phrase_for_log = (
                    section_phrases.get(sidx, "")[:80]
                )
                logger.info(
                    f"[IMG-TRACE] SEC_CITE_INDEX research={research_id} "
                    f"sec={sidx} sec_phrase=\"{sec_phrase_for_log}\" "
                    f"cite_nums={list(nums)}"
                )
        # Event 2 (closes G3): URL_HTML_MAP — which URLs are in
        # url_to_html, how long each html_content is, and which
        # source-code path populated it (findings vs all_links).
        # Aug 6 had html_covered=2 with no per-URL provenance.
        findings_urls = {
            sr.get("url") or sr.get("link")
            for finding in results.get("findings", []) or []
            for sr in finding.get("search_results", []) or []
        }
        all_links_urls = {
            r.get("link") or r.get("url")
            for r in results.get("all_links_of_system") or []
        }
        for url, html in url_to_html.items():
            if url in findings_urls:
                src = "findings"
            elif url in all_links_urls:
                src = "all_links"
            else:
                src = "deferred_backfill"
            logger.info(
                f"[IMG-TRACE] URL_HTML_MAP research={research_id} "
                f"url={url} html_len={len(html)} src={src}"
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
            _log_end(research_id, "empty")
            return clean_markdown

        # Per-section entity pool + embeddings for the semantic gate.
        sections = _split_sections(clean_markdown)
        levels = _section_levels(clean_markdown)
        entity_pool = semantic_matcher.build_report_entity_pool(clean_markdown)
        section_phrases: dict[int, str] = {}
        # 2026-08-28 (research 199acec3): the previous "skip if no
        # entities" gate left entity-poor subsections (e.g. "（四）南
        # 京路步行街：中华商业第一街" with no extracted entities) with
        # NO cosine scoring at all — even heading-only phrase was
        # rejected. The new canonical-section-phrase ignores entities
        # entirely (semantic_matcher.py:228-241), so we always call
        # it when a heading exists.
        for sidx, entities in entity_pool.items():
            if sidx >= len(sections):
                continue
            parent = _find_parent_heading(sections, levels, sidx)
            phrase = semantic_matcher._canonical_section_phrase(
                sections[sidx][0], entities, parent_heading=parent
            )
            if phrase:
                section_phrases[sidx] = phrase
        # Now that section_phrases is populated, fire Event 1 (SEC_CITE_INDEX)
        # defined above (lazy emit to avoid UnboundLocalError).
        _emit_sec_cite_index()
        # Sections whose citations are ALL darkweb take the fast path
        # below and never need a section embedding — excluding them
        # here means a darkweb-only run never loads the sentence-
        # transformer model. Sections with no citations at all (e.g.
        # the References block, which leaks into the entity pool) are
        # also skipped: they can never bind an image. Sections with at
        # least one clearnet citation keep the semantic gate (their
        # darkweb citations still take the per-image fast path).
        _dark_sec = {
            sidx
            for sidx, nums in section_to_nums.items()
            if nums
            and all(is_darkweb_url(num_to_url.get(n) or "") for n in nums)
        }
        # Pre-embed section phrases (one vector per cited section).
        try:
            section_vecs: dict[int, list[float]] = {
                sidx: list(_encode_phrase_cached(p))
                for sidx, p in section_phrases.items()
                if sidx not in _dark_sec and section_to_nums.get(sidx)
            }
        except Exception as exc:
            logger.warning(
                f"[IMG-TRACE] SEMANTIC_MATCH_FAILED research={research_id} "
                f"reason={type(exc).__name__}: {exc}"
            )
            _log_end(research_id, "empty")
            return clean_markdown

        threshold = alt_similarity_threshold
        # 2026-09-13 (research c34cb8fa): multi-surface scoring inputs.
        # Section headings under the topic-profile directive are fixed
        # templates without named entities, so alt-vs-heading cosine is
        # weak. Each candidate is additionally scored against (a) its
        # cited reference's textual passage and (b) the original
        # research query; the gate takes the max. Both extra surfaces
        # share language/entity density with real alt text, while
        # filename-derived token-soup alts score low on them (verified
        # 2026-09-13: real alt 0.27→0.59 on ref-text/query surfaces,
        # junk alt 0.65→0.32).
        url_to_text = build_url_text_index(results)
        text_vecs_cache: dict[str, list[float]] = {}
        query_vec: Optional[list[float]] = None
        # Alt-vector cache: one entry per image URL, filled by the
        # scoring loop below, consumed by the spread pass in
        # _build_placements (re-seating over-cap images into other
        # same-cite sections without re-encoding).
        alt_vecs_cache: dict[str, list[float]] = {}
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
        binding: dict[str, list[tuple[int, int, float]]] = {}

        # Stage 2: extract images from each cited source, single-section
        # semantic gate against the citation's section.
        # Darkweb (.onion) sources take a two-check fast path: no
        # alt-vs-section semantic gate at all. An image is adopted iff
        # (1) the cite_num's ref_url is same-origin (eTLD+1, i.e. the
        # same .onion site) as the page the image was extracted from,
        # and (2) it survives the same min-dimension logo/icon filter
        # the extractor applies on clearnet. Clearnet citations keep
        # the threshold pipeline below.
        #
        # 2026-08-24 (research 0043b4af, FPV穿越机): every cite existed
        # in the References block (num_to_url populated) and 13 had
        # HTML (url_to_html populated) — but 0 body sections had any
        # inline [N] markers (the LLM only listed the references in
        # the trailing block, never as inline citations in the prose).
        # Result: section_to_nums[0..28] were all [] and the for-loop
        # below iterated 0 times, producing ELIGIBLE_BANK total=0 and
        # END status=empty with no diagnostic. The diagnostic line
        # below surfaces this case so operators can see WHY a research
        # landed at status=empty: num_to_url > 0 but no (sidx, num)
        # pair exists in section_to_nums.
        total_pairs = sum(len(v) for v in section_to_nums.values())
        pairs_with_html = sum(
            1
            for nums in section_to_nums.values()
            for num in nums
            if (url := num_to_url.get(num)) and url in url_to_html
        )
        if total_pairs == 0 and num_to_url and url_to_html:
            logger.info(
                f"[IMG-TRACE] NO_SECTION_BINDING research={research_id} "
                f"num_to_url={len(num_to_url)} "
                f"url_to_html={len(url_to_html)} "
                f"section_to_nums_total_pairs=0 "
                f"reason=no_inline_cite_markers_in_body "
                f"all_cites_in_references_block_only"
            )
        for sidx, nums in section_to_nums.items():
            if not nums:
                continue
            if not any(
                is_darkweb_url(num_to_url.get(n) or "") for n in nums
            ):
                if sidx not in section_vecs:
                    continue
            if sidx in section_vecs:
                sec_vec = section_vecs[sidx]
            else:
                sec_vec = None
            # Pre-canonicalised section phrase (heading + entities
            # joined with spaces). Captured here so the CANDIDATE_SCORED
            # event below can log the original text the model encoded
            # for this section, instead of an opaque hash. Truncated
            # to 200 chars so a long entity list doesn't blow up the
            # log.
            sec_phrase_text = (section_phrases.get(sidx, "") or "")[:200]
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
                        f"img_alt={(cand.alt or '')!r} "
                        f"img_url={cand.url} "
                        f"img_source_url={cand.source_url}"
                    )
                # Event 5 (closes G4): SEC_BINDING — per (sec, cite,
                # url) triple trace. Aug 6 emitted CITATION_CANDIDATES
                # but operators could not link kept_alts back to the
                # section. This event records cand_count + sample_alts.
                kept_alts = [
                    (cand.alt or "")
                    for cand in imgs
                    if cand.alt and cand.alt.strip()
                ]
                logger.info(
                    f"[IMG-TRACE] SEC_BINDING research={research_id} "
                    f"sec={sidx} sec_phrase=\"{sec_phrase_text!s}\" "
                    f"cite_num={num} ref_url={url} "
                    f"cand_count={len(cand_lines)} kept_alts={len(kept_alts)} "
                    f"sample_alts={kept_alts[:3]!r}"
                )
                logger.info(
                    f"[IMG-TRACE] CITATION_CANDIDATES research={research_id} "
                    f"cite_num={num} ref_url={url} sec={sidx} "
                    f"count={len(cand_lines)} "
                    + ("| ".join(cand_lines) if cand_lines else "(no_alt_candidates)")
                )
                kept = 0
                dropped_low = 0
                dropped_src = 0
                dropped_small = 0
                dropped_alt = 0
                # Loaded lazily: on a darkweb citation no image ever
                # reaches the semantic path below, so the sentence-
                # transformer model is never touched.
                dark_cite = is_darkweb_url(url or "")
                # Loaded lazily: on a darkweb citation no image ever
                # reaches the semantic path below, so the sentence-
                # transformer model is never touched.
                model = None if dark_cite else semantic_matcher.get_model()
                for img in imgs:
                    if dark_cite:
                        # Absolute floor (2026-08-23 policy): images
                        # known to be below 50x50 px are NEVER adopted —
                        # applies before every override including
                        # messaging evidence. Known dimensions come
                        # from width/height attrs or dims embedded in
                        # the alt ('40x40'). Unknown dimensions stay
                        # lenient (decided later by the size rule).
                        _fw, _fh = img.width, img.height
                        if (_fw is None or _fh is None) and img.alt:
                            _fd = _dims_from_alt(img.alt)
                            if _fd:
                                _fw = _fw if _fw is not None else _fd[0]
                                _fh = _fh if _fh is not None else _fd[1]
                        if (_fw is not None and _fw < _MIN_DIM) or (
                            _fh is not None and _fh < _MIN_DIM
                        ):
                            dropped_small += 1
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                                f"img_alt={(img.alt or '')!r} "
                                f"img_url={img.url} "
                                f"img_source_url={img.source_url} "
                                f"cite_num={num} "
                                f"ref_url={url} "
                                f"sec={sidx} score=0.00 "
                                f"reason=too_small"
                            )
                            continue
                        # Darkweb fast path — messaging-evidence
                        # override (2026-08-23): alt/filename mentions
                        # of messaging platforms / email addresses are
                        # contact-channel intel — force-adopt BEFORE
                        # any filter, even if same-origin fails (a
                        # vendor's QQ card hotlinked from an image host
                        # is still evidence).
                        if _is_messaging_evidence(img):
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_KEPT research={research_id} "
                                f"img_alt={(img.alt or '')!r} "
                                f"img_url={img.url} "
                                f"img_source_url={img.source_url} "
                                f"cite_num={num} "
                                f"ref_url={url} "
                                f"sec={sidx} score=0.00"
                            )
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_SCORED_DETAIL research={research_id} "
                                f"sec={sidx} cite_num={num} ref_url={url} "
                                f"img_alt={(img.alt or '')!r} img_url={img.url} "
                                f"score=0.00 decision=keep reason=messaging_evidence"
                            )
                            bank.add([img])
                            binding.setdefault(img.url, []).append(
                                (num, sidx, 0.0)
                            )
                            kept += 1
                            continue
                        # Darkweb fast path — two checks only.
                        if not domains_match(img.source_url, url):
                            dropped_src += 1
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                                f"img_alt={(img.alt or '')!r} "
                                f"img_url={img.url} "
                                f"img_source_url={img.source_url} "
                                f"cite_num={num} "
                                f"ref_url={url} "
                                f"sec={sidx} score=0.00 "
                                f"reason=source_not_same_origin"
                            )
                            continue
                        # Meaningless alt — STRICT list only (pure UI
                        # vocabulary like 'home'/'logo'). Dimension-
                        # style and short ambiguous alts pass through;
                        # icon-sized images are killed by the size
                        # check below instead (2026-08-22 policy).
                        if _alt_is_meaningless(img.alt or ""):
                            dropped_alt += 1
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                                f"img_alt={(img.alt or '')!r} "
                                f"img_url={img.url} "
                                f"img_source_url={img.source_url} "
                                f"cite_num={num} "
                                f"ref_url={url} "
                                f"sec={sidx} score=0.00 "
                                f"reason=meaningless_alt"
                            )
                            continue
                        # Size filter — STRICT: when the width/height
                        # attributes are absent, a dimension embedded
                        # in the alt ('300x300', 'image 740x555') is
                        # authoritative; below the icon threshold it
                        # is a logo/badge and dropped.
                        _w, _h = img.width, img.height
                        if (_w is None or _h is None) and img.alt:
                            _alt_dims = _dims_from_alt(img.alt)
                            if _alt_dims:
                                _w = _w if _w is not None else _alt_dims[0]
                                _h = _h if _h is not None else _alt_dims[1]
                        if (
                            _w is not None and _w < _MIN_DIM
                        ) or (
                            _h is not None and _h < _MIN_DIM
                        ):
                            dropped_small += 1
                            logger.info(
                                f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                                f"img_alt={(img.alt or '')!r} "
                                f"img_url={img.url} "
                                f"img_source_url={img.source_url} "
                                f"cite_num={num} "
                                f"ref_url={url} "
                                f"sec={sidx} score=0.00 "
                                f"reason=too_small"
                            )
                            continue
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_SCORED research={research_id} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} ref_url={url} sec={sidx} "
                            f"sec_phrase_text={sec_phrase_text!r}"
                        )
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_KEPT research={research_id} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} "
                            f"sec={sidx} score=0.00"
                        )
                        bank.add([img])
                        binding.setdefault(img.url, []).append((num, sidx, 0.0))
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_SCORED_DETAIL research={research_id} "
                            f"sec={sidx} cite_num={num} ref_url={url} "
                            f"img_alt={(img.alt or '')!r} img_url={img.url} "
                            f"score=0.00 decision=keep reason=darkweb_same_origin"
                        )
                        logger.info(
                            f"[IMG-TRACE] BIND_ADOPTED research={research_id} "
                            f"sec={sidx} cite_num={num} ref_url={url} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"action=attach reason=darkweb_same_origin"
                        )
                        kept += 1
                        continue
                    if not (img.alt and img.alt.strip()):
                        # Event 6: CANDIDATE_NO_ALT — promoted from
                        # DEBUG to INFO (closes G4 alt-missing path).
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_NO_ALT research={research_id} "
                            f"src_url={url} img_url={img.url}"
                        )
                        continue
                    if not _alt_has_semantic_content(img.alt):
                        # 2026-09-13 (research c34cb8fa): filename-derived
                        # token-soup alts ('ldgjhfknbaicem1496932883')
                        # embed near Chinese section phrases (cosine
                        # 0.61-0.65, verified offline) — the multilingual
                        # encoder's OOV corner. Such alts are rejected
                        # BEFORE the encoder, deterministically.
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} sec={sidx} score=0.00 "
                            f"reason=alt_junk_token"
                        )
                        continue
                    raw = model.encode([img.alt], normalize_embeddings=True)[0]
                    alt_vec = list(raw.tolist()) if hasattr(raw, "tolist") else list(raw)
                    # Cache the alt vector for the spread pass (it
                    # re-scores over-cap images against OTHER sections
                    # without re-running the encoder).
                    alt_vecs_cache.setdefault(img.url, alt_vec)
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
                        f"img_alt={(img.alt or '')!r} "
                        f"img_url={img.url} "
                        f"img_source_url={img.source_url} "
                        f"cite_num={num} ref_url={url} sec={sidx} "
                        f"sec_phrase_text={sec_phrase_text!r}"
                    )
                    # Multi-surface gate (2026-09-13): max over the
                    # section phrase, the cited reference's textual
                    # passage, and the original research query. The
                    # heading surface alone under-scores real alts when
                    # the report language differs from the alt language
                    # and headings are entity-free templates.
                    surface_scores: dict[str, float] = {
                        "sec": round_score(_cosine(alt_vec, sec_vec))
                    }
                    ref_text = url_to_text.get(url)
                    if ref_text:
                        t_vec = text_vecs_cache.get(url)
                        if t_vec is None:
                            t_raw = model.encode(
                                [ref_text], normalize_embeddings=True
                            )[0]
                            t_vec = (
                                list(t_raw.tolist())
                                if hasattr(t_raw, "tolist")
                                else list(t_raw)
                            )
                            text_vecs_cache[url] = t_vec
                        surface_scores["ref_text"] = round_score(
                            _cosine(alt_vec, t_vec)
                        )
                    if research_query and research_query.strip():
                        if query_vec is None:
                            q_raw = model.encode(
                                [research_query], normalize_embeddings=True
                            )[0]
                            query_vec = (
                                list(q_raw.tolist())
                                if hasattr(q_raw, "tolist")
                                else list(q_raw)
                            )
                        surface_scores["query"] = round_score(
                            _cosine(alt_vec, query_vec)
                        )
                    surface = max(surface_scores, key=surface_scores.get)
                    score = surface_scores[surface]
                    if score >= round_score(threshold):
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
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} "
                            f"sec={sidx} score={score:.2f}"
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
                            (num, sidx, score)
                        )
                        # Event 7 kept-side: per-image decision + reason.
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_SCORED_DETAIL research={research_id} "
                            f"sec={sidx} cite_num={num} ref_url={url} "
                            f"img_alt={(img.alt or '')!r} img_url={img.url} "
                            f"score={score:.2f} decision=keep reason=phrase_similarity "
                            f"surface={surface} surfaces={surface_scores}"
                        )
                        # Event 8 (closes G4): BIND_ADOPTED —
                        # final-stage adoption trail. Aug 6 had no
                        # observable per-image attach/dup-skip decision.
                        # Carries the same five-key schema as other
                        # IMG-TRACE events so a single grep unions
                        # kept/dropped/adopted streams. Renamed from
                        # PLACEMENT_DECISION to BIND_ADOPTED so it
                        # doesn't substring-match the existing PLACEMENT
                        # event in the schema tests.
                        logger.info(
                            f"[IMG-TRACE] BIND_ADOPTED research={research_id} "
                            f"sec={sidx} cite_num={num} ref_url={url} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"action=attach reason=kept_by_threshold"
                        )
                        kept += 1
                    else:
                        # Same five-key schema as CANDIDATE_KEPT so a
                        # log parser can union the two streams into a
                        # complete per-image decision table. Drops get
                        # ``kept=0`` reason baked in so consumers don't
                        # need to fork on the event name to know what
                        # happened.
                        # OBS-D: promoted from debug to info so a
                        # single grep over `[IMG-TRACE] CANDIDATE_` can
                        # union KEPT + DROPPED into a complete per-image
                        # decision table without depending on operator
                        # log-level config. The five-key schema
                        # (img_alt / img_url / img_source_url / cite_num
                        # / ref_url) is preserved verbatim.
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_DROPPED research={research_id} "
                            f"img_alt={(img.alt or '')!r} "
                            f"img_url={img.url} "
                            f"img_source_url={img.source_url} "
                            f"cite_num={num} "
                            f"ref_url={url} "
                            f"sec={sidx} score={score:.2f} "
                            f"reason=below_threshold"
                        )
                        # Event 7 (closes G4): CANDIDATE_SCORED_DETAIL
                        # — per-image score + decision + reason for
                        # both kept and dropped paths.
                        logger.info(
                            f"[IMG-TRACE] CANDIDATE_SCORED_DETAIL research={research_id} "
                            f"sec={sidx} cite_num={num} ref_url={url} "
                            f"img_alt={(img.alt or '')!r} img_url={img.url} "
                            f"score={score:.2f} decision=drop reason=below_threshold "
                            f"surface={surface} surfaces={surface_scores}"
                        )
                        dropped_low += 1
                logger.info(
                    f"[IMG-TRACE] CITATION_MATCH research={research_id} "
                    f"num={num} imgs={len(imgs)} kept={kept} "
                    f"low_similarity={dropped_low} "
                    f"source_not_same_origin={dropped_src} "
                    f"too_small={dropped_small} "
                    f"meaningless_alt={dropped_alt}"
                )

        if not bank.all_urls():
            logger.info(
                f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} total=0"
            )
            _log_end(research_id, "empty")
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
        # Build placements from binding (url -> list[(num, sec, score)])
        # — multi-bind semantics: a single image URL can bind to several
        # sections if its source page is cited in each. We emit one
        # placement per (url, sec) pair, capped at SECTION_IMAGE_CAP per
        # section by score desc, sorted by section index for stable
        # in-section ordering. The post-insert dedup pass
        # (``_dedupe_images`` below) collapses any duplicate
        # ``![alt](url)`` instances produced by the multi-bind,
        # keeping the FIRST occurrence in document order.
        # Includes EMPTY-alt images (darkweb fast path adopts them by
        # size rules — alt text is not a filter, 2026-08-22 policy);
        # their caption falls back to the source page title.
        bank_by_url = {img.url: img for img in bank.all_images()}
        # Darkweb-engine run (main or auxiliary): raise the per-section
        # cap to 5 (2026-08-23 policy). Detected from the bank's source
        # URLs — any .onion source makes the run darkweb for capping.
        _any_dark = any(
            is_darkweb_url(getattr(img, "source_url", "") or "")
            for img in bank.all_images()
        )
        placements = _build_placements(
            binding,
            bank_by_url,
            _caption_fallback=True,
            darkweb=_any_dark,
            spread=not _any_dark,
            num_to_url=num_to_url,
            section_to_nums=section_to_nums,
            section_vecs=section_vecs,
            section_phrases=section_phrases,
            alt_vecs=alt_vecs_cache,
            # Spread gate: relaxed below the strict adoption threshold
            # ("some similarity" — heading/parent overlap), floored at
            # 0 so a misconfigured low main threshold can't turn the
            # spread pass into an unconditional dump.
            spread_relaxed_threshold=max(
                0.0, threshold - _SPREAD_THRESHOLD_RELAX
            ),
        )
        for sidx, p_url, p_alt in placements:
            # Find the (num, sec) pair for this placement. If the
            # URL is bound to multiple (num, sec), pick the one that
            # matches this placement's section.
            matching = [
                (n, sec) for n, sec, _score in binding.get(p_url, [])
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
                f"img_alt={(p_alt or '')!r} "
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
        # Darkweb-only MDINSERT probe: same counters as the existing
        # INSERT line under the darkweb namespace. Gated on at least
        # one placement source_url being .onion so clearnet runs emit
        # no event. ``sections`` is the count of distinct section
        # indices that received at least one image. The placement
        # tuple is unpacked positionally as
        # ``(alt, img_url, source_url, cite_num, ref_url, sec_idx)``
        # matching the PLACEMENT log line at line 618 above.
        _src_urls = [p[2] for p in placements]
        if any(is_darkweb_url(u) for u in _src_urls):
            _sections_with_img = len({p[5] for p in placements})
            logger.info(
                f"[IMG-TRACE-DARKWEB] MDINSERT research={research_id} "
                f"placements={len(placements)} sections={_sections_with_img}"
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
        # Darkweb-only PERSIST probe: emit a parallel rollup under the
        # IMG-TRACE-DARKWEB namespace so a single grep reconstructs the
        # chosen/succeeded/failed counters for one task. Gated on at
        # least one chosen URL being .onion — same shape as the
        # existing PERSIST line, no value duplication, just a
        # darkweb-grep-friendly mirror.
        if any(is_darkweb_url(u) for u in chosen):
            logger.info(
                f"[IMG-TRACE-DARKWEB] PERSIST research={research_id} "
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
            # binding[url] is a list of (num, sidx, score) triples. Pick
            # the FIRST pair (matches dedup_images' keep-first semantic
            # for the displayed image — the persisted image is the one
            # whose citation the reader saw in the body).
            pairs = binding.get(url) or [(None, None, None)]
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
                f"img_alt={(img.alt or '')!r} "
                f"img_url={img.url} "
                f"img_source_url={img.source_url} "
                f"cite_num={num} "
                f"ref_url={img.source_url} "
                f"local_route={route} "
                f"local_path={local_path}"
            )
        _log_end(research_id, "ok")
        return enhanced
    except Exception:
        logger.exception(
            "Image post-processing failed; returning clean markdown"
        )
        _log_end(research_id, "error")
        return clean_markdown
