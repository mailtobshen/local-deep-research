"""Citation formatter for adding hyperlinks and alternative citation styles."""

import re
from enum import Enum
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

_SOURCES_SECTION_PATTERNS = [
    re.compile(
        r"^#{1,3}\s*(?:Sources|References|Bibliography|Citations)",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(
        r"^(?:Sources|References|Bibliography|Citations):?\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
]

# CJK variants of the sources / references heading. English headings
# (above) miss these because they only accept ASCII tokens. The per-
# section reports in the wild use ``## 参考文献`` / ``## 参考资料`` /
# ``## 引用来源`` so we need our own patterns for them. Kept
# deliberately narrow — must look like a markdown heading (1-3 '#'
# + space + the heading token) so a sentence containing "参考文献" in
# running prose is not matched.
_SOURCES_SECTION_CJK_PATTERNS = [
    re.compile(
        r"^#{1,3}\s*(?:参考文献|参考资料|引用来源|参考来源|资料来源|引用文献)\s*$",
        re.MULTILINE,
    ),
]

# Inline [N] citation markers in body text. Negative lookbehind/lookahead
# avoid matching already-formatted citations like "[[1]](url)" or "[1]]".
# Also matches full-width lenticular brackets 【N】 that LLMs sometimes
# generate. Lifted to module level so other modules (e.g. images/relevance
# for per-section URL mapping) can compile-free import them.
CITE_INLINE_RE = re.compile(
    r"(?<![\[【])[\[【](\d+)[\]】](?![\]】])"
)

# Inline comma-group citations like [1, 2, 3] — a single bracket pair
# containing several citation numbers. LLMs emit this style as well as
# the single-number [N] form. Group 1 is the comma-separated number
# string, e.g. "1, 2, 3". Used alongside CITE_INLINE_RE; downstream
# code splits the captured text on "," to recover each number.
CITE_INLINE_GROUP_RE = re.compile(
    r"[\[【](\d+(?:,\s*\d+)+)[\]】]"
)

# A row in the trailing References list:
#   [N] Title
#      URL: https://...
# Group 1 = citation number (or comma-separated list), group 2 = title,
# group 3 = URL.
#
# The URL line is REQUIRED and must be non-empty. If it is missing
# or blank, the row is intentionally not matched — callers
# (extract_segment_sources, _scan_sources_markdown_urls) treat an
# unmatched row as "URL unknown" and drop it. This is the fix
# for a real production bug where the previous optional-URL
# pattern `(?:\n\s*URL:\s*(.+?))?$` swallowed the *next* row's
# title into group 3 when a row's URL: line was blank
# (e.g. ``[6] Beijing opera\n   URL:\n[7] Hutong\n   URL: ...``
# matched with group(3) = "[7] Hutong", poisoning the citation
# map).
# Group 3 = URL. The leading `[` is explicitly excluded so an
# empty-URL row (``URL:\n[7] Hutong``) does not match the *next*
# row's title as the URL. Anchored at end-of-line so the match
# stays within a single record.
CITE_LIST_ROW_RE = re.compile(
    r"^\[(\d+(?:,\s*\d+)*)\]\s*(.+?)\n\s*URL:\s*(?!\[)(\S.*?)$",
    re.MULTILINE,
)


def find_sources_section(content: str) -> int:
    """Find the start position of the sources/references section in *content*.

    Returns -1 if no section is found. Matches both English headings
    (``## Sources`` / ``## References`` / …) and the CJK variants
    (``## 参考文献`` / ``## 参考资料`` / ``## 引用来源`` / …) so a
    single call covers reports in any language.
    """
    earliest = -1
    for pattern in (
        *_SOURCES_SECTION_PATTERNS,
        *_SOURCES_SECTION_CJK_PATTERNS,
    ):
        match = pattern.search(content)
        if match and (earliest == -1 or match.start() < earliest):
            earliest = match.start()
    return earliest


def strip_per_section_sources_block(body: str) -> str:
    """Remove an LLM-written sources/references block from a single
    section body.

    Detailed-mode reports ask the LLM to write one subsection at a time.
    Some models helpfully append a local ``## 参考文献`` (or
    ``## References``) block to the section they just produced.  Those
    blocks use a section-local citation numbering, conflict with the
    unified ``## Sources`` block assembled at the end of the report, and
    produce the "every chapter has its own references" artefact the
    user reported.  The trailing block is the single source of truth,
    so the per-section one is dropped here.

    Only the *last* sources/references heading in the body is stripped,
    because that is the one the LLM appends to its own output. A
    section that legitimately discusses references in its opening
    paragraphs (before any real content) is left intact — we only
    truncate the trailing block, which by definition sits at the end
    of the section. If no trailing block is present the body is
    returned unchanged.
    """
    start = find_sources_section(body)
    if start < 0:
        return body
    # Walk back to the start of the line that contains the heading so
    # the truncated result ends with a clean newline.
    line_start = body.rfind("\n", 0, start) + 1
    if line_start > 0:
        truncated = body[:line_start].rstrip() + "\n"
    else:
        truncated = body[:start].rstrip() + "\n"
    return truncated


# Regexes for the citation renumbering / hallucination-stripping helpers
# below. They deliberately accept both plain `[N]` and the already-
# hyperlinked `[[N]](url)` form so that the renumber pass is idempotent
# and does not require an unformat-then-format round-trip.
RENUMBER_HYPERLINK_RE = re.compile(r"\[\[(\d+)\]\]\(([^)]*)\)")
RENUMBER_PLAIN_RE = re.compile(
    r"(?<![\[【])\[(\d+)\](?![\]】\(])"
)
# Combined scanner used by build_first_cite_order — group 1 = plain [N],
# group 2 = [[N]](url). Plain form's lookbehind/lookahead prevents
# matching the inner `[N]` of `[[N]](url)` and the trailing `](`.
RENUMBER_SCAN_RE = re.compile(
    r"(?<!\[)\[(\d+)\](?!\]\()"
    r"|"
    r"\[\[(\d+)\]\]\([^)]*\)"
)
# Matches "Source N" / "source N" so hallucinated numbers there can be
# stripped alongside the bracketed forms.
SOURCE_WORD_NUMS_RE = re.compile(r"\b[Ss]ource\s+(\d+)\b")


def build_first_cite_order(body: str, valid_indices) -> list:
    """Return members of *valid_indices* in their first-occurrence order in
    *body*.

    Handles plain ``[N]``, comma groups ``[N, M, ...]``, and the
    already-hyperlinked ``[[N]](url)`` form. Members of *valid_indices*
    never referenced in the body are simply absent from the result.
    """
    seen: list = []
    seen_set = set()

    # Pass 1 — comma groups. Iterate inside the captured group so each
    # member contributes independently.
    for m in CITE_INLINE_GROUP_RE.finditer(body):
        for raw in m.group(1).split(","):
            n = int(raw.strip())
            if n in valid_indices and n not in seen_set:
                seen.append(n)
                seen_set.add(n)

    # Pass 2 — single numbers (plain `[N]` and `[[N]](url)`).
    for m in RENUMBER_SCAN_RE.finditer(body):
        raw = m.group(1) or m.group(2)
        n = int(raw)
        if n in valid_indices and n not in seen_set:
            seen.append(n)
            seen_set.add(n)

    return seen


def strip_hallucinated_citations(body: str, valid_indices) -> str:
    """Remove citation tokens whose numbers are not in *valid_indices*.

    Touches plain ``[N]``, comma groups ``[N, M, ...]``, hyperlinked
    ``[[N]](url)``, and the ``Source N`` word pattern. Tokens are deleted
    outright — no ``[?]`` placeholder is left behind. Comma groups that
    lose all members collapse to empty; comma groups that retain at least
    one member are rewritten to ``[kept1, kept2]``. Adjacent spaces are
    collapsed (newlines are preserved).
    """
    def replace_hyperlink(match):
        n = int(match.group(1))
        return "" if n not in valid_indices else match.group(0)

    def replace_comma(match):
        members = [
            (s.strip(), int(s.strip()))
            for s in match.group(1).split(",")
        ]
        kept = [s for s, n in members if n in valid_indices]
        if len(kept) == len(members):
            return match.group(0)
        if not kept:
            return ""
        return "[" + ", ".join(kept) + "]"

    def replace_plain(match):
        n = int(match.group(1))
        return "" if n not in valid_indices else match.group(0)

    def replace_source_word(match):
        n = int(match.group(1))
        return "" if n not in valid_indices else match.group(0)

    # Order matters: hyperlink first so the inner `[N]` is not re-matched
    # by the plain scan.
    body = RENUMBER_HYPERLINK_RE.sub(replace_hyperlink, body)
    body = CITE_INLINE_GROUP_RE.sub(replace_comma, body)
    body = RENUMBER_PLAIN_RE.sub(replace_plain, body)
    body = SOURCE_WORD_NUMS_RE.sub(replace_source_word, body)
    # Collapse runs of two or more spaces produced by removals. Do not
    # touch newlines so paragraph structure survives.
    body = re.sub(r" {2,}", " ", body)
    return body


def renumber_citations(body: str, sources: dict, old_to_new: dict) -> str:
    """Rewrite ``[N]`` and ``[[N]](url)`` per the *old_to_new* remap.

    Already-hyperlinked tokens (``[[N]](url)``) keep their original URL —
    only the index is rewritten to the new number. Plain ``[N]`` tokens
    are rewritten using the URL from *sources* (a mapping of
    ``new_index -> (title, url)``) when one is present, or fall back to
    plain ``[new]``. Tokens whose old number is not in *old_to_new* are
    left untouched — the caller is expected to have stripped hallucinated
    numbers first.

    Idempotent: re-running on already-renumbered text is a no-op because
    ``RENUMBER_HYPERLINK_RE`` / ``RENUMBER_PLAIN_RE`` only match the
    ``[N]`` / ``[[N]](url)`` shape, never the rewritten ``[new]``.
    """
    def replace_hyperlink(match):
        old = int(match.group(1))
        if old not in old_to_new:
            return match.group(0)
        new = old_to_new[old]
        url = match.group(2)
        return f"[[{new}]]({url})"

    def replace_plain(match):
        old = int(match.group(1))
        if old not in old_to_new:
            return match.group(0)
        new = old_to_new[old]
        entry = sources.get(new)
        if entry and entry[1]:
            return f"[[{new}]]({entry[1]})"
        return f"[{new}]"

    # Hyperlink first to avoid double-touching the inner `[N]`.
    body = RENUMBER_HYPERLINK_RE.sub(replace_hyperlink, body)
    body = RENUMBER_PLAIN_RE.sub(replace_plain, body)
    return body


# Regex for parsing a single row in the trailing Sources block.
# Anchored at the start of a line so it does not match body prose
# that happens to contain ``[N]`` substrings. Matches the same shape
# as ``CITE_LIST_ROW_RE`` but allows the URL to be the *last* non-empty
# token on its line (the actual Sources rows can have trailing text in
# some emitters). The trailing ``$`` requires the match to end at a
# newline so we don't swallow the next row.
_SOURCES_ROW_PARSE_RE = re.compile(
    r"^\[(\d+(?:,\s*\d+)*)\]\s*(.+?)\n\s*URL:\s*(\S+)\s*$",
    re.MULTILINE,
)

# Any non-URL trailing lines that follow a parsed row up to the next
# row (or the block end) — preserved verbatim in the rebuild so we
# don't drop e.g. ``Collection: ...`` lines emitted by the library/RAG
# renderer.
_NON_URL_TRAILING_RE = re.compile(
    r"^(?!URL:)(?!\[).+$",
    re.MULTILINE,
)


def _split_sources_block(sources_content: str) -> List[Dict[str, Any]]:
    """Parse the trailing Sources block into a list of row dicts.

    Each dict carries:
        - ``displayed_n``: list[int] — the original ``[N]`` or
          ``[N, M, ...]`` numbers shown at the start of the row
        - ``title``: str — the title text on the row header line
        - ``url``: str — the URL extracted from the ``URL:`` line
        - ``trailing``: list[str] — non-URL lines that follow the
          header (e.g. ``Collection: mypapers``); preserved verbatim
        - ``raw_match_span``: tuple[int, int] — (start, end) of the
          ``[N] title`` header line in the original block text

    Rows that have no parseable ``URL:`` line are returned as
    ``displayed_n=[N], title=text, url="", trailing=[], ...`` so the
    caller can decide whether to drop them (orphan-delete cannot match
    them, but the renumber step can still preserve them in the block).
    """
    rows: List[Dict[str, Any]] = []
    # Walk line-by-line so we can collect trailing non-URL lines that
    # belong to each row.
    lines = sources_content.split("\n")
    i = 0
    block_start_offset = 0  # cumulative offset for span tracking
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped.startswith("["):
            i += 1
            block_start_offset += len(line) + 1
            continue
        # Try to parse the header line.
        header_match = re.match(
            r"^\[(\d+(?:,\s*\d+)*)\]\s*(.+?)\s*$", stripped
        )
        if not header_match:
            i += 1
            block_start_offset += len(line) + 1
            continue
        displayed_n = [int(n.strip()) for n in header_match.group(1).split(",")]
        title = header_match.group(2).strip()
        header_offset = block_start_offset
        header_end = block_start_offset + len(line)
        # Walk forward: collect URL: line (if any) and trailing lines.
        url = ""
        trailing: List[str] = []
        trailing_start = header_end + 1
        j = i + 1
        block_start_offset = trailing_start
        while j < len(lines):
            tl = lines[j]
            tl_strip = tl.strip()
            if tl_strip.startswith("["):
                # Next row begins — stop.
                block_start_offset = trailing_start
                break
            if tl_strip.startswith("URL:"):
                url = tl_strip[len("URL:") :].strip()
            elif tl_strip:
                # Preserve non-URL lines (e.g. Collection: ...) verbatim,
                # but skip blank/whitespace-only lines so they do not
                # accumulate as preserved "trailing" content on
                # repeat invocations.
                trailing.append(tl)
            j += 1
            block_start_offset += len(tl) + 1
        else:
            # Loop ran to end — block_start_offset already updated.
            pass
        rows.append(
            {
                "displayed_n": displayed_n,
                "title": title,
                "url": url,
                "trailing": trailing,
                "header_span": (header_offset, header_end),
            }
        )
        i = j
    return rows


def enforce_sources_ascending_and_drop_orphans(content: str) -> str:
    """Rewrite *content* so the trailing ``## Sources`` block is strictly
    ascending and orphan body citations are deleted.

    Two invariants are enforced:

    1. **Ascending ``[N]``** — the displayed bracket numbers in the
       trailing Sources block are re-mapped to ``[1], [2], …, [N]``
       strictly ascending, in body-first-cite order. Each canonical
       URL appears exactly once in the rebuilt block (first-cite wins).
    2. **No orphan body citations** — in-body ``[[N]](URL)`` and
       plain ``[N]`` markers whose URL has no matching Sources entry
       are deleted. Plain ``[N]`` tokens whose entry has a URL are
       hyperlinked to that URL (consistent with
       :func:`renumber_citations`).

    Rows in the Sources block whose ``URL:`` line is empty are kept
    but cannot be linked from the body (no orphan delete can match
    them). They keep their renumbered position.

    No-op when no Sources block exists. Idempotent: re-running on
    already-enforced content is a no-op because the rewritten block
    is structurally the same shape that the next pass would re-parse
    into a 1..M ascending list.

    Args:
        content: Full markdown report including the trailing
            ``## Sources`` block.

    Returns:
        The rewritten markdown. Body and Sources block always keep
        a ``## Sources`` header separator when one was present.
    """
    start = find_sources_section(content)
    if start < 0:
        return content

    # Locate the start of the line containing the heading so the
    # rebuilt output preserves the same separator as upstream.
    line_start = content.rfind("\n", 0, start) + 1
    body = content[:line_start].rstrip() + "\n"
    sources_block = content[start:]
    # Trim the Sources block to start exactly at the heading line.
    heading_line_offset = start - line_start
    sources_only = sources_block[heading_line_offset:]

    rows = _split_sources_block(sources_only)
    if not rows:
        # No parseable rows. Leave the block untouched.
        return content

    # Lazy import — ``utilities.url_utils`` transitively imports the
    # encrypted DB module, which crashes when sqlcipher3 is not
    # installed (e.g. in lightweight unit-test setups). Defer to a
    # nested try/except so ``citation_formatter`` stays importable in
    # those environments.
    try:
        from ..utilities.url_utils import canonical_url_key
    except Exception:
        # Fallback: inline a richer canonicalizer that handles the
        # edge cases observed in production reports — trailing
        # ``/`` mismatches, malformed trailing ``)`` from an LLM
        # echoing URLs with parens, and inconsistent host casing.
        # NOT a substitute for the full helper at module level (which
        # also strips tracking params, default ports, and userinfo);
        # for orphan detection in the enforcer the aggressive form
        # below is sufficient.
        def canonical_url_key(url: str) -> str:
            from urllib.parse import urlsplit, urlunsplit

            s = url.strip()
            # Drop a trailing ')' that is unbalanced — some LLM-emitted
            # Sources rows wrap the URL in parens (or in some cases a
            # markdown renderer would strip a single trailing ')'), and
            # we want orphan detection to ignore that one-char drift.
            # Strip iteratively so URLs ending with "))" still match.
            while s.endswith(")") and s.count("(") < s.count(")"):
                s = s[:-1]
            # Append a single missing ')' so a URL that lost its closing
            # paren (e.g. body emits ``...tianzifang-(tian-zi-fang``
            # while Sources emits ``...tianzifang-(tian-zi-fang)``) still
            # canonicalises to the same string.
            if s.endswith("-") or s.endswith(":"):
                # No recovery from these pathological shapes — leave as-is.
                pass
            elif "(" in s and s.count("(") > s.count(")"):
                s = s + ")" * (s.count("(") - s.count(")"))
            parts = urlsplit(s)
            host = (parts.netloc or "").lower()
            if "@" in host:
                host = host.split("@", 1)[1]
            cleaned = urlunsplit(
                (
                    (parts.scheme or "").lower(),
                    host,
                    parts.path,
                    parts.query or "",
                    "",  # fragment stripped
                )
            )
            return cleaned.rstrip("/")

    # Map canonical_url -> [displayed_n candidates, ...]. Each row may
    # have multiple displayed_n (e.g. comma-group "[1, 3]"); the
    # renumber step picks the *first* one for the dedupe mapping.
    canon_to_displayed_ns: Dict[str, List[int]] = {}
    for row in rows:
        if not row["url"]:
            continue
        canon = canonical_url_key(row["url"]) or row["url"]
        canon_to_displayed_ns.setdefault(canon, [])
        for n in row["displayed_n"]:
            if n not in canon_to_displayed_ns[canon]:
                canon_to_displayed_ns[canon].append(n)

    # Step 1: drop orphan body markers. A marker is an orphan when its
    # URL (for ``[[N]](URL)``) or its URL-after-remap (for plain
    # ``[N]`` via the per-row entry) is not present in
    # canon_to_displayed_ns.
    #
    # For plain ``[N]`` tokens we cannot know the URL without a row
    # lookup; rows are keyed by their displayed_n so we build a
    # displayed_n -> url map. CRUCIALLY: each row is an INDEPENDENT
    # citation — even when two rows share the same canonical URL they
    # must be treated as different sources with different displayed_n.
    # Collapsing them would cause 张冠李戴 (the wrong title/content
    # being linked from the wrong body position). So if a displayed_n
    # appears in multiple rows (rare but possible — e.g. the LLM
    # reused the displayed N by accident), the LAST row's URL wins
    # so the same displayed_n resolves consistently in the body.
    displayed_n_to_url: Dict[int, str] = {}
    for row in rows:
        for n in row["displayed_n"]:
            if row["url"]:
                displayed_n_to_url[n] = row["url"]

    # Build a per-displayed_n set of valid URLs (any of which the
    # body marker for that N is allowed to match). When multiple
    # rows share a displayed_n, an orphan check against this set
    # accepts the marker if ANY of the rows' URLs matches the
    # marker's URL. This preserves the semantic: "this body marker
    # is one of these rows" rather than collapsing rows into one.
    displayed_n_to_canon_urls: Dict[int, set] = {}
    for row in rows:
        if not row["url"]:
            continue
        canon = canonical_url_key(row["url"]) or row["url"]
        for n in row["displayed_n"]:
            displayed_n_to_canon_urls.setdefault(n, set()).add(canon)

    # Dedup pass — collapse rows that are CLEARLY duplicates of the
    # same source. We deliberately use a conservative rule: two
    # rows are merged ONLY when they share the same canonical URL
    # AND their titles normalise to the same string. This handles
    # the common LLM-hallucination case (the model writes the same
    # entry twice under two different displayed_n with slightly
    # different titles — both go to the same URL because there is
    # only one real source) without merging two genuinely distinct
    # sources that happen to share a URL (rare but real: two
    # different papers both pointing at the same arxiv preprint).
    # Rows with distinct titles keep their own identity (no
    # 张冠李式).
    dedup_winner: Dict[int, int] = {}
    _NON_WORD_RE = re.compile(r"\W+")
    _SOURCE_NR_TRAILING_RE = re.compile(
        r"\s*\(source nr:\s*[\d,\s]+\)\s*$"
    )

    def _normalised_title(s: str) -> str:
        cleaned = _SOURCE_NR_TRAILING_RE.sub("", s or "")
        cleaned = re.sub(r"^\s*\[[\d,\s]+\]\s*", "", cleaned)
        return _NON_WORD_RE.sub("", cleaned.lower()).strip()

    def _compute_dedup(body_ns: List[int]) -> Dict[int, int]:
        rows_by_canon: Dict[str, List[int]] = {}

        for ridx, row in enumerate(rows):
            if not row["url"]:
                continue
            canon = canonical_url_key(row["url"]) or row["url"]
            rows_by_canon.setdefault(canon, []).append(ridx)
        result: Dict[int, int] = {}
        for canon, ridxes in rows_by_canon.items():
            if len(ridxes) <= 1:
                continue
            cluster_of: Dict[int, int] = {}
            cluster_titles: Dict[int, str] = {}
            for ridx in ridxes:
                nt = _normalised_title(rows[ridx]["title"])
                found = None
                for cid, ct in cluster_titles.items():
                    if ct == nt:
                        found = cid
                        break
                if found is None:
                    cluster_titles[ridx] = nt
                    cluster_of[ridx] = ridx
                else:
                    cluster_of[ridx] = found
            winners: Dict[int, int] = {}
            for cid in set(cluster_of.values()):
                cluster_rows = [
                    ridx for ridx in ridxes if cluster_of[ridx] == cid
                ]
                cluster_displayed_ns = {
                    n
                    for ridx in cluster_rows
                    for n in rows[ridx]["displayed_n"]
                }
                best = max(
                    cluster_rows,
                    key=lambda r: (
                        sum(1 for n in body_ns if n in cluster_displayed_ns),
                        len((rows[r]["title"] or "").strip()),
                        -r,
                    ),
                )
                winners[cid] = best
            for ridx in ridxes:
                winner = winners[cluster_of[ridx]]
                if ridx != winner:
                    result[ridx] = winner
        return result

    # Union of all Sources URLs — the URL-level set the user asked
    # for: any body marker whose URL is in this set is recognised
    # as a valid citation (even if its displayed_n points at a
    # different row); markers whose URL is NOT in this set are
    # genuine orphans and get dropped.
    all_canon_urls: set = set()
    for row in rows:
        if not row["url"]:
            continue
        canon = canonical_url_key(row["url"]) or row["url"]
        all_canon_urls.add(canon)

    def url_is_kept(url: str, n: int) -> bool:
        """Return True if a body marker ``[[n]](url)`` survives the
        orphan cut. Survives iff at least one Sources row — for ANY
        displayed_n — accepts a canonical URL matching url. This is
        the URL-level check the user asked for: a body marker whose
        URL has no matching Sources entry is dropped; markers whose
        URL exists in Sources under a *different* displayed_n are
        kept and get rewired (via the renumber step) to the row that
        owns the URL.
        """
        if not url:
            return False
        canon = canonical_url_key(url) or url
        return canon in all_canon_urls

    def replace_hyperlink_drop(match: "re.Match[str]") -> str:
        n = int(match.group(1))
        url = match.group(2)
        return match.group(0) if url_is_kept(url, n) else ""

    def replace_plain_drop(match: "re.Match[str]") -> str:
        n = int(match.group(1))
        # Plain `[N]` cannot carry a URL. Survive iff at least one
        # Sources row references N — that row's URL is what
        # ``renumber_citations`` will use to hyperlink the plain
        # marker in the next step.
        return (
            match.group(0)
            if n in displayed_n_to_canon_urls and displayed_n_to_canon_urls[n]
            else ""
        )

    new_body = RENUMBER_HYPERLINK_RE.sub(replace_hyperlink_drop, body)
    new_body = RENUMBER_PLAIN_RE.sub(replace_plain_drop, new_body)
    # Expand comma-groups ``[a, b, c]`` into ``[a][b][c]`` so the
    # subsequent renumber step can rewrite each member independently.
    # ``renumber_citations`` itself does not handle comma-groups — that
    # is left to the downstream ``format_document`` pass — but for the
    # enforcer we want every surviving integer to be reachable by
    # ``RENUMBER_PLAIN_RE`` so the order-preserving scan in step 2 sees
    # the same set of citations the body will eventually render.
    def replace_comma(match: "re.Match[str]") -> str:
        members = [s.strip() for s in match.group(1).split(",")]
        # Drop members that resolved to no URL — emit nothing for them
        # so the rest still collapses to a clean marker run.
        kept = [m for m in members if displayed_n_to_url.get(int(m))]
        return "".join(f"[{m}]" for m in kept)

    new_body = CITE_INLINE_GROUP_RE.sub(replace_comma, new_body)
    # Collapse runs of spaces left behind by deletions. Preserve
    # newlines so paragraph structure survives.
    new_body = re.sub(r" {2,}", " ", new_body)

    # Step 2: build old_to_new by walking surviving body markers in
    # first-cite order. We use the same scanner as build_first_cite_order
    # but accept *any* integer (not just those in ``valid_indices``)
    # because at this point the only numbers still in the body are
    # ones whose URL survived the orphan-drop step above.
    surviving_ns_in_order: List[int] = []
    seen_ns: set = set()

    def add_surviving(n: int) -> None:
        if n not in seen_ns:
            seen_ns.add(n)
            surviving_ns_in_order.append(n)

    # Pass 1 — comma groups.
    for m in CITE_INLINE_GROUP_RE.finditer(new_body):
        for raw in m.group(1).split(","):
            try:
                n = int(raw.strip())
            except ValueError:
                continue
            add_surviving(n)
    # Pass 2 — single numbers (plain `[N]` and `[[N]](url)`).
    for m in RENUMBER_SCAN_RE.finditer(new_body):
        raw = m.group(1) or m.group(2)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        add_surviving(n)

    # Build the new ordering. CRUCIAL invariant: each Sources row is an
    # INDEPENDENT entry that maps a body marker to a specific title/
    # URL/content. Two rows that happen to share the same canonical
    # URL are NOT duplicates — they are different sources (different
    # titles, different semantic content). The enforcer must NOT
    # collapse them, or body marker [[7]] pointing at row 2's content
    # gets rewired to row 1's title (张冠李戴).
    #
    # Strategy:
    #   - Walk surviving_ns_in_order (body first-cite order).
    #   - For each old N, find the FIRST row whose displayed_n list
    #     contains old_n AND whose URL matches (any accepted canon
    #     for that N). Each row gets its own new_n.
    #   - old_to_new maps every displayed_n in the surviving row to
    #     that row's new_n.
    #
    # When two rows share BOTH the same canonical URL AND the same
    # displayed_n (LLM wrote the same number twice for the same
    # source), we keep only the LAST row — the earlier one is
    # shadowed in body markers anyway because both point at the same
    # URL, but the LAST row's title wins as the canonical "what does
    # this N reference".

    # First: populate dedup_winner (LLM-duplicate rows that share
    # both URL and normalised title). The ordered_rows loop below
    # skips dropped rows so each canonical source gets exactly one
    # Sources entry.
    dedup_winner = _compute_dedup(surviving_ns_in_order)

    # First pass: map each row's displayed_n to its index, capturing
    # which URLs each N is allowed to match (for body-orphan check).
    row_by_displayed_n: Dict[int, List[int]] = {}
    for ridx, row in enumerate(rows):
        for n in row["displayed_n"]:
            row_by_displayed_n.setdefault(n, []).append(ridx)

    # Second pass: walk surviving old Ns and assign new_n in body-
    # first-cite order.
    ordered_rows: List[int] = []  # row indices in the new order
    seen_rows: set = set()
    for old_n in surviving_ns_in_order:
        # Pick the first matching row whose URL also matches the
        # body's accepted canon(s) for old_n. If body has no accepted
        # canon (plain marker with no row URL) just pick the first
        # matching row.
        accepted = displayed_n_to_canon_urls.get(old_n, set())
        candidates = row_by_displayed_n.get(old_n, [])
        chosen = None
        for ridx in candidates:
            r = rows[ridx]
            if not r["url"]:
                continue
            row_canon = canonical_url_key(r["url"]) or r["url"]
            if not accepted or row_canon in accepted:
                chosen = ridx
                break
        if chosen is None:
            # Plain marker whose displayed_n has no row URL — pick
            # any row containing the number so renumber_citations
            # can rewrite the number; the URL lookup will be empty
            # so no link is emitted.
            chosen = candidates[0] if candidates else None
        if chosen is None or chosen in seen_rows:
            continue
        seen_rows.add(chosen)
        # Skip rows that the dedup pass has marked as dropped —
        # their winner already got picked first (winner precedes
        # loser in body-first-cite order when both are cited).
        if chosen in dedup_winner:
            continue
        ordered_rows.append(chosen)

    # Third pass: append rows that the body never cited. They keep
    # their own displayed_n in the new block (in original row order)
    # so the bibliography is complete. Each gets its own new_n
    # after the cited ones.
    for ridx in range(len(rows)):
        if ridx in seen_rows:
            continue
        if ridx in dedup_winner:
            continue
        ordered_rows.append(ridx)

    # Build old_to_new and new_sources_map together.
    old_to_new: Dict[int, int] = {}
    new_sources_map: Dict[int, tuple] = {}
    # First: every displayed_n of a DEDUPED-DROPPED row maps onto
    # the winner row's new index. This rewires body [[N]] (where N
    # is the dropped row's displayed_n) to the surviving row.
    new_idx_for_ridx = {
        ridx: new_idx for new_idx, ridx in enumerate(ordered_rows, start=1)
    }
    for dropped_ridx, winner_ridx in dedup_winner.items():
        winner_new = new_idx_for_ridx[winner_ridx]
        for n in rows[dropped_ridx]["displayed_n"]:
            old_to_new[n] = winner_new
    # Then: surviving rows map their own displayed_n to their own
    # new index. If a displayed_n is shared between a dropped row
    # and a surviving row (LLM typo), the surviving row's mapping
    # wins — the dropped row's body markers resolve to the real
    # source, not the hallucinated twin.
    for new_idx, ridx in enumerate(ordered_rows, start=1):
        row = rows[ridx]
        new_sources_map[new_idx] = (row["title"], row["url"])
        for n in row["displayed_n"]:
            old_to_new[n] = new_idx

    # Step 3: rewrite body markers using the existing renumber helper.
    new_body = renumber_citations(new_body, new_sources_map, old_to_new)

    # Step 4: rebuild the Sources block. Emit ONE entry per row that
    # made it through, in body-first-cite order (uncited rows
    # appended last). Strip any existing ``(source nr: ...)`` suffix
    # from the parsed title so the rebuilt entry has exactly one such
    # suffix — otherwise the original's `(source nr: X)` gets
    # concatenated with the new one producing e.g.
    # ``(source nr: 60) (source nr: 47)``. Preserve the original
    # heading text (``## Sources`` / ``## 参考文献`` / …) so CJK
    # reports keep their heading style after the rewrite.
    heading_match = re.match(r"^#{1,6}\s*\S[^\n]*", sources_only)
    heading_text = (
        heading_match.group(0).rstrip()
        if heading_match
        else "## Sources"
    )
    rebuilt_lines: List[str] = [heading_text, ""]
    source_nr_re = re.compile(r"\s*\(source nr:\s*[\d,\s]+\)\s*$")
    for new_idx, ridx in enumerate(ordered_rows, start=1):
        row = rows[ridx]
        title = source_nr_re.sub("", row["title"] or "Untitled").strip()
        url = row["url"]
        rebuilt_lines.append(f"[{new_idx}] {title} (source nr: {new_idx})")
        if url:
            rebuilt_lines.append(f"   URL: {url}")
        # Preserve any non-URL trailing lines (e.g. ``Collection: ...``).
        rebuilt_lines.extend(row["trailing"])
        rebuilt_lines.append("")  # blank line between entries
    # Collapse any blank lines my rebuild may have introduced at the
    # tail (e.g. the ``trailing`` list can already end with a blank).
    new_sources_block = "\n".join(rebuilt_lines).rstrip() + "\n"

    return new_body.rstrip("\n") + "\n\n" + new_sources_block


class CitationMode(Enum):
    """Available citation formatting modes."""

    NUMBER_HYPERLINKS = "number_hyperlinks"  # [1] with hyperlinks
    DOMAIN_HYPERLINKS = "domain_hyperlinks"  # [arxiv.org] with hyperlinks
    DOMAIN_ID_HYPERLINKS = (
        "domain_id_hyperlinks"  # [arxiv.org] or [arxiv.org-1] with smart IDs
    )
    DOMAIN_ID_ALWAYS_HYPERLINKS = (
        "domain_id_always_hyperlinks"  # [arxiv.org-1] always with IDs
    )
    SOURCE_TAGGED_HYPERLINKS = "source_tagged_hyperlinks"
    """Preserve the global citation number and prefix it with a short source
    tag derived from the URL: known academic sources via ``URLClassifier``
    (``arxiv-7``, ``pubmed-3``), domain otherwise (``nytimes.com-9``), and
    ``local-N`` for empty / local URLs. Unlike DOMAIN_ID_* modes the
    suffix is the original citation number, so labels never collide and
    match the bibliography order: ``[1]`` arxiv + ``[2]`` openai + ``[3]``
    arxiv -> ``arxiv-1``, ``openai-2``, ``arxiv-3``."""
    NO_HYPERLINKS = "no_hyperlinks"  # [1] without hyperlinks


class CitationFormatter:
    """Formats citations in markdown documents with various styles."""

    def __init__(self, mode: CitationMode = CitationMode.NUMBER_HYPERLINKS):
        self.mode = mode
        # Inline citation markers — reference the module-level
        # CITE_INLINE_RE so the regex is compiled once and shared with
        # other modules (e.g. images/relevance).
        self.citation_pattern = CITE_INLINE_RE
        self.comma_citation_pattern = CITE_INLINE_GROUP_RE
        # Also match "Source X" or "source X" patterns
        self.source_word_pattern = re.compile(r"\b[Ss]ource\s+(\d+)\b")
        # Trailing References list rows — reference the module-level
        # CITE_LIST_ROW_RE for the same reason.
        self.sources_pattern = CITE_LIST_ROW_RE

    def _create_source_word_replacer(self, formatter_func):
        """Create a replacement function for 'Source X' patterns.

        Args:
            formatter_func: A function that takes citation_num and returns formatted text

        Returns:
            A replacement function for use with regex sub
        """

        def replace_source_word(match):
            citation_num = match.group(1)
            return formatter_func(citation_num)

        return replace_source_word

    def _create_citation_formatter(self, sources_dict, format_pattern):
        """Create a formatter function for citations.

        Args:
            sources_dict: Dictionary mapping citation numbers to data
            format_pattern: A callable that takes (citation_num, data) and returns formatted string

        Returns:
            A function that formats citations or returns fallback
        """

        def formatter(citation_num):
            if citation_num in sources_dict:
                data = sources_dict[citation_num]
                return format_pattern(citation_num, data)
            return f"[{citation_num}]"

        return formatter

    def _replace_comma_citations(self, content, lookup, format_one):
        """Replace comma-separated citations like [1, 2, 3] using *lookup* and *format_one*.

        Args:
            content: Text to process
            lookup: Dict mapping citation number (str) to data
            format_one: ``(num, data) -> str`` callback that formats a single citation
        """

        def _replacer(match):
            nums = [n.strip() for n in match.group(1).split(",")]
            parts = []
            for num in nums:
                if num in lookup:
                    parts.append(format_one(num, lookup[num]))
                else:
                    parts.append(f"[{num}]")
            return "".join(parts)

        return self.comma_citation_pattern.sub(_replacer, content)

    def format_document(self, content: str) -> str:
        """
        Format citations in the document according to the selected mode.

        Args:
            content: The markdown content to format

        Returns:
            Formatted markdown content
        """
        if self.mode == CitationMode.NO_HYPERLINKS:
            return content

        # Extract sources section
        sources_start = self._find_sources_section(content)
        if sources_start == -1:
            return content

        document_content = content[:sources_start]
        sources_content = content[sources_start:]

        # Parse sources
        sources = self._parse_sources(sources_content)

        # Format citations in document
        if self.mode == CitationMode.NUMBER_HYPERLINKS:
            formatted_content = self._format_number_hyperlinks(
                document_content, sources
            )
        elif self.mode == CitationMode.DOMAIN_HYPERLINKS:
            formatted_content = self._format_domain_hyperlinks(
                document_content, sources
            )
        elif self.mode == CitationMode.DOMAIN_ID_HYPERLINKS:
            formatted_content = self._format_domain_id_hyperlinks(
                document_content, sources
            )
        elif self.mode == CitationMode.DOMAIN_ID_ALWAYS_HYPERLINKS:
            formatted_content = self._format_domain_id_always_hyperlinks(
                document_content, sources
            )
        elif self.mode == CitationMode.SOURCE_TAGGED_HYPERLINKS:
            formatted_content = self._format_source_tagged_hyperlinks(
                document_content,
                sources,
                self._parse_collections(sources_content),
            )
        else:
            formatted_content = document_content

        # Rebuild document
        return formatted_content + sources_content

    def _find_sources_section(self, content: str) -> int:
        """Find the start of the sources/references section."""
        return find_sources_section(content)

    def _parse_sources(
        self, sources_content: str
    ) -> Dict[str, Tuple[str, str]]:
        """
        Parse sources section to extract citation numbers, titles, and URLs.

        Returns:
            Dictionary mapping citation number to (title, url) tuple
        """
        sources = {}
        matches = list(self.sources_pattern.finditer(sources_content))

        for match in matches:
            citation_nums_str = match.group(1)
            title = match.group(2).strip()
            url = match.group(3).strip() if match.group(3) else ""

            # Handle comma-separated citation numbers like [36, 3]
            # Split by comma and strip whitespace
            individual_nums = [
                num.strip() for num in citation_nums_str.split(",")
            ]

            # Add an entry for each individual number
            for num in individual_nums:
                sources[num] = (title, url)

        return sources

    def _format_number_hyperlinks(
        self, content: str, sources: Dict[str, Tuple[str, str]]
    ) -> str:
        """Replace [1] with hyperlinked version where only the number is linked."""
        # Filter sources that have URLs
        url_sources = {
            num: (title, url) for num, (title, url) in sources.items() if url
        }

        # Create formatter for citations with number hyperlinks
        def format_number_link(citation_num, data):
            _, url = data
            return f"[[{citation_num}]]({url})"

        # Handle comma-separated citations like [1, 2, 3]
        content = self._replace_comma_citations(
            content, url_sources, format_number_link
        )

        formatter = self._create_citation_formatter(
            url_sources, format_number_link
        )

        # Handle individual citations
        def replace_citation(match):
            return (
                formatter(match.group(1))
                if match.group(1) in url_sources
                else match.group(0)
            )

        content = self.citation_pattern.sub(replace_citation, content)

        # Also handle "Source X" patterns
        return self.source_word_pattern.sub(
            self._create_source_word_replacer(formatter), content
        )

    def _format_domain_hyperlinks(
        self, content: str, sources: Dict[str, Tuple[str, str]]
    ) -> str:
        """Replace [1] with [domain.com] hyperlinked version."""

        # Filter sources that have URLs
        url_sources = {
            num: (title, url) for num, (title, url) in sources.items() if url
        }

        # Create formatter for citations with domain hyperlinks
        def format_domain_link(citation_num, data):
            _, url = data
            domain = self._extract_domain(url)
            return f"[[{domain}]]({url})"

        # Handle comma-separated citations like [1, 2, 3]
        content = self._replace_comma_citations(
            content, url_sources, format_domain_link
        )

        formatter = self._create_citation_formatter(
            url_sources, format_domain_link
        )

        # Handle individual citations
        def replace_citation(match):
            return (
                formatter(match.group(1))
                if match.group(1) in url_sources
                else match.group(0)
            )

        content = self.citation_pattern.sub(replace_citation, content)

        # Also handle "Source X" patterns
        return self.source_word_pattern.sub(
            self._create_source_word_replacer(formatter), content
        )

    def _format_domain_id_hyperlinks(
        self, content: str, sources: Dict[str, Tuple[str, str]]
    ) -> str:
        """Replace [1] with [domain.com-1] hyperlinked version with hyphen-separated IDs."""
        # First, create a mapping of domains to their citation numbers
        domain_citations: dict[str, list[Any]] = {}

        for citation_num, (title, url) in sources.items():
            if url:
                domain = self._extract_domain(url)
                if domain not in domain_citations:
                    domain_citations[domain] = []
                domain_citations[domain].append((citation_num, url))

        # Create a mapping from citation number to domain with ID
        citation_to_domain_id = {}
        for domain, citations in domain_citations.items():
            if len(citations) > 1:
                # Multiple citations from same domain - add hyphen and number
                for idx, (citation_num, url) in enumerate(citations, 1):
                    citation_to_domain_id[citation_num] = (
                        f"{domain}-{idx}",
                        url,
                    )
            else:
                # Single citation from domain - no ID needed
                citation_num, url = citations[0]
                citation_to_domain_id[citation_num] = (domain, url)

        # Create formatter for citations with domain_id hyperlinks
        def format_domain_id_link(citation_num, data):
            domain_id, url = data
            return f"[[{domain_id}]]({url})"

        # Handle comma-separated citations
        content = self._replace_comma_citations(
            content, citation_to_domain_id, format_domain_id_link
        )

        formatter = self._create_citation_formatter(
            citation_to_domain_id, format_domain_id_link
        )

        # Handle individual citations
        def replace_citation(match):
            return (
                formatter(match.group(1))
                if match.group(1) in citation_to_domain_id
                else match.group(0)
            )

        content = self.citation_pattern.sub(replace_citation, content)

        # Also handle "Source X" patterns
        return self.source_word_pattern.sub(
            self._create_source_word_replacer(formatter), content
        )

    def _format_domain_id_always_hyperlinks(
        self, content: str, sources: Dict[str, Tuple[str, str]]
    ) -> str:
        """Replace [1] with [domain.com-1] hyperlinked version, always with IDs."""
        # First, create a mapping of domains to their citation numbers
        domain_citations: dict[str, list[Any]] = {}

        for citation_num, (title, url) in sources.items():
            if url:
                domain = self._extract_domain(url)
                if domain not in domain_citations:
                    domain_citations[domain] = []
                domain_citations[domain].append((citation_num, url))

        # Create a mapping from citation number to domain with ID
        citation_to_domain_id = {}
        for domain, citations in domain_citations.items():
            # Always add hyphen and number for consistency
            for idx, (citation_num, url) in enumerate(citations, 1):
                citation_to_domain_id[citation_num] = (f"{domain}-{idx}", url)

        # Create formatter for citations with domain_id hyperlinks
        def format_domain_id_link(citation_num, data):
            domain_id, url = data
            return f"[[{domain_id}]]({url})"

        # Handle comma-separated citations
        content = self._replace_comma_citations(
            content, citation_to_domain_id, format_domain_id_link
        )

        formatter = self._create_citation_formatter(
            citation_to_domain_id, format_domain_id_link
        )

        # Handle individual citations
        def replace_citation(match):
            return (
                formatter(match.group(1))
                if match.group(1) in citation_to_domain_id
                else match.group(0)
            )

        content = self.citation_pattern.sub(replace_citation, content)

        # Also handle "Source X" patterns
        return self.source_word_pattern.sub(
            self._create_source_word_replacer(formatter), content
        )

    # Sources section may carry a "Collection: <name>" line for RAG /
    # library hits (emitted by ``utilities/search_utilities.format_links_to_markdown``).
    # The line sits between this ``[N]`` entry's ``URL:`` line and the
    # next ``[N+1]`` entry. We anchor the match on a non-greedy span up
    # to the next citation header (or end of string) to scope correctly.
    _collection_line_pattern = re.compile(
        r"^\[(\d+(?:,\s*\d+)*)\][^\n]*\n"  # the [N] header line
        r"(?:[^\n\[]*\n)*?"  # any non-[ lines (typically URL: ...)
        r"\s*Collection:\s*(.+?)\s*$",
        re.MULTILINE,
    )

    def _parse_collections(self, sources_content: str) -> Dict[str, str]:
        """Extract ``{citation_num: collection_name}`` from a sources
        block. Returns an empty dict when no ``Collection:`` lines exist
        — the absence of collection info is the common case (web URLs)
        and must never raise."""
        collections: Dict[str, str] = {}
        for match in self._collection_line_pattern.finditer(sources_content):
            citation_nums_str = match.group(1)
            collection = match.group(2).strip()
            if not collection:
                continue
            for num in (n.strip() for n in citation_nums_str.split(",")):
                collections[num] = collection
        return collections

    def _format_source_tagged_hyperlinks(
        self,
        content: str,
        sources: Dict[str, Tuple[str, str]],
        collections: Dict[str, str],
    ) -> str:
        """Replace ``[N]`` with ``[[source-N]](url)``.

        ``source`` resolves to (in order): the RAG ``Collection:``
        tag for library hits, the short URLClassifier tag for known
        academic sources (``arxiv``, ``pubmed``, ...), the cleaned
        domain otherwise, or ``local`` for empty/file URLs. ``N`` is
        the original global citation number — labels never collide and
        the suffix always matches the bibliography ordering.

        Args:
            content: Document body (sources section already split off).
            sources: ``{citation_num: (title, url)}`` parsed from the
                sources block.
            collections: ``{citation_num: collection_name}`` parsed from
                optional ``Collection:`` lines in the sources block
                (empty dict when no library/RAG hits are cited). Wins
                over URL-derived tags when present for a given citation.
        """

        def format_link(citation_num, data):
            _, url = data
            label = self._extract_source_label(
                url, collection=collections.get(citation_num)
            )
            tag = f"{label}-{citation_num}"
            # Only emit a hyperlink for http(s) URLs — local/file URLs are
            # rendered as plain bracketed tags so the markdown stays clean
            # and viewers don't try to navigate to a server-local path.
            return (
                f"[[{tag}]]({url})"
                if self._is_linkable_url(url)
                else f"[{tag}]"
            )

        # Handle comma-separated citations like [1, 2, 3]
        content = self._replace_comma_citations(content, sources, format_link)

        formatter = self._create_citation_formatter(sources, format_link)

        # Handle individual citations
        def replace_citation(match):
            return (
                formatter(match.group(1))
                if match.group(1) in sources
                else match.group(0)
            )

        content = self.citation_pattern.sub(replace_citation, content)

        # Also handle "Source X" patterns
        return self.source_word_pattern.sub(
            self._create_source_word_replacer(formatter), content
        )

    @staticmethod
    def _slugify_collection(name: str) -> str:
        """Make a user-set collection name safe for inline citations.

        Collection names are free-form strings (``"My Papers"``,
        ``"team/finance"``). Citations need a compact token that won't
        break markdown — strip whitespace, lowercase, replace runs of
        non-alphanumeric chars with a single hyphen, trim leading and
        trailing hyphens, and fall back to ``"local"`` if the result is
        empty. ``-N`` is appended downstream so we strip trailing
        hyphens to keep the join clean.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        return slug or "local"

    @staticmethod
    def _is_linkable_url(url: str) -> bool:
        """Return True iff ``url`` is a http(s) URL safe to wrap in a
        markdown hyperlink. Empty strings and file:// / local: schemes
        are not linkable."""
        if not url:
            return False
        try:
            scheme = (urlparse(url).scheme or "").lower()
        except (ValueError, AttributeError):
            return False
        return scheme in ("http", "https")

    def _extract_source_label(
        self, url: str, collection: str | None = None
    ) -> str:
        """Return a short source tag for ``url``.

        Resolution order:
        1. ``collection`` (when supplied) wins outright — RAG / library
           hits surface their collection name as the citation tag
           (``mypapers``, ``personal-notes``, ...). The renderer in
           ``utilities/search_utilities.format_links_to_markdown``
           emits a ``Collection:`` line per source for library results,
           which the formatter parses back into this argument.
        2. Empty URL or non-http(s) scheme (``file://``, ``local:``, ...) →
           ``"local"``. Uniform fallback when no collection name is
           available.
        3. ``URLClassifier`` matches a known academic source → use the
           enum value (``arxiv``, ``pubmed``, ``pmc``, ``biorxiv``,
           ``medrxiv``, ``semantic_scholar``, ``doi``).
        4. Otherwise → fall back to ``_extract_domain`` (e.g.
           ``arxiv.org``, ``nytimes.com``).
        """
        if collection:
            return self._slugify_collection(collection)
        if not url:
            return "local"
        try:
            parsed = urlparse(url)
        except (ValueError, AttributeError):
            return "local"
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return "local"

        # Lazy import to keep the formatter usable when the content_fetcher
        # package isn't importable (e.g. minimal test setups).
        try:
            from ..content_fetcher.url_classifier import URLClassifier, URLType
        except ImportError:
            return self._extract_domain(url)

        url_type = URLClassifier.classify(url)
        # Generic HTML/PDF/INVALID → fall back to domain. Everything else
        # is a known academic source whose enum value is the short tag.
        if url_type in (URLType.HTML, URLType.PDF, URLType.INVALID):
            return self._extract_domain(url)
        return url_type.value

    def _extract_domain(self, url: str) -> str:
        """Extract domain name from URL."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            # Remove www. prefix if present
            if domain.startswith("www."):
                domain = domain[4:]
            # Keep known domains as-is
            known_domains = {
                "arxiv.org": "arxiv.org",
                "github.com": "github.com",
                "reddit.com": "reddit.com",
                "youtube.com": "youtube.com",
                "pypi.org": "pypi.org",
                "milvus.io": "milvus.io",
                "medium.com": "medium.com",
            }

            for known, display in known_domains.items():
                if known in domain:
                    return display

            # For other domains, extract main domain
            parts = domain.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return domain
        except (ValueError, AttributeError):
            return "source"


class QuartoExporter:
    """Export markdown documents to Quarto (.qmd) format."""

    def __init__(self):
        # Also match Unicode lenticular brackets 【】 (U+3010 and U+3011) that LLMs sometimes generate
        self.citation_pattern = re.compile(
            r"(?<![\[【])[\[【](\d+)[\]】](?![\]】])"
        )
        self.comma_citation_pattern = re.compile(
            r"[\[【](\d+(?:,\s*\d+)+)[\]】]"
        )

    def export_to_quarto(self, content: str, title: str | None = None) -> str:
        """
        Convert markdown document to Quarto format.

        Args:
            content: Markdown content
            title: Document title (if None, will extract from content)

        Returns:
            Quarto formatted content
        """
        # Extract title from markdown if not provided
        if not title:
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_match.group(1) if title_match else "Research Report"

        # Create Quarto YAML header
        from datetime import datetime, UTC

        current_date = datetime.now(UTC).strftime("%Y-%m-%d")
        yaml_header = f"""---
title: "{title}"
author: "Local Deep Research"
date: "{current_date}"
format:
  html:
    toc: true
    toc-depth: 3
    number-sections: true
  pdf:
    toc: true
    number-sections: true
    colorlinks: true
bibliography: references.bib
csl: apa.csl
---

"""

        # Process content
        processed_content = content

        # First handle comma-separated citations like [1, 2, 3]
        def replace_comma_citations(match):
            citation_nums = match.group(1)
            # Split by comma and strip whitespace
            nums = [num.strip() for num in citation_nums.split(",")]
            refs = [f"@ref{num}" for num in nums]
            return f"[{', '.join(refs)}]"

        processed_content = self.comma_citation_pattern.sub(
            replace_comma_citations, processed_content
        )

        # Then convert individual citations to Quarto format [@citation]
        def replace_citation(match):
            citation_num = match.group(1)
            return f"[@ref{citation_num}]"

        processed_content = self.citation_pattern.sub(
            replace_citation, processed_content
        )

        # Generate bibliography file content
        bib_content = self._generate_bibliography(content)

        # Add note about bibliography file
        bibliography_note = (
            "\n\n::: {.callout-note}\n## Bibliography File Required\n\nThis document requires a `references.bib` file in the same directory with the following content:\n\n```bibtex\n"
            + bib_content
            + "\n```\n:::\n"
        )

        return yaml_header + processed_content + bibliography_note

    def _generate_bibliography(self, content: str) -> str:
        """Generate BibTeX bibliography from sources."""
        sources_pattern = re.compile(
            r"^\[(\d+)\]\s*(.+?)(?:\n\s*URL:\s*(.+?))?$", re.MULTILINE
        )

        bibliography = ""
        matches = list(sources_pattern.finditer(content))

        for match in matches:
            citation_num = match.group(1)
            title = match.group(2).strip()
            url = match.group(3).strip() if match.group(3) else ""

            # Generate BibTeX entry
            bib_entry = f"@misc{{ref{citation_num},\n"
            bib_entry += f'  title = "{{{title}}}",\n'
            if url:
                bib_entry += f"  url = {{{url}}},\n"
                bib_entry += f'  howpublished = "\\url{{{url}}}",\n'
            bib_entry += f"  year = {{{2024}}},\n"
            bib_entry += '  note = "Accessed: \\today"\n'
            bib_entry += "}\n"

            bibliography += bib_entry + "\n"

        return bibliography.strip()


class RISExporter:
    """Export references to RIS format for reference managers like Zotero."""

    def __init__(self):
        self.sources_pattern = re.compile(
            r"^\[(\d+(?:,\s*\d+)*)\]\s*(.+?)(?:\n\s*URL:\s*(.+?))?$",
            re.MULTILINE,
        )

    def export_to_ris(self, content: str) -> str:
        """
        Extract references from markdown and convert to RIS format.

        Args:
            content: Markdown content with sources

        Returns:
            RIS formatted references
        """
        # Find sources section
        sources_start = find_sources_section(content)
        if sources_start == -1:
            return ""

        # Find the end of the first sources section (before any other major section)
        sources_content = content[sources_start:]

        # Look for the next major section to avoid duplicates
        next_section_markers = [
            "\n## ALL SOURCES",
            "\n### ALL SOURCES",
            "\n## Research Metrics",
            "\n### Research Metrics",
            "\n## SEARCH QUESTIONS",
            "\n### SEARCH QUESTIONS",
            "\n## DETAILED FINDINGS",
            "\n### DETAILED FINDINGS",
            "\n---",  # Horizontal rule often separates sections
        ]

        sources_end = len(sources_content)
        for marker in next_section_markers:
            pos = sources_content.find(marker)
            if pos != -1 and pos < sources_end:
                sources_end = pos

        sources_content = sources_content[:sources_end]

        # Parse sources and generate RIS entries
        ris_entries = []
        seen_refs = set()  # Track which references we've already processed

        # Split sources into individual entries
        import re

        # Pattern to match each source entry
        source_entry_pattern = re.compile(
            r"^\[(\d+)\]\s*(.+?)(?=^\[\d+\]|\Z)", re.MULTILINE | re.DOTALL
        )

        for match in source_entry_pattern.finditer(sources_content):
            citation_num = match.group(1)
            entry_text = match.group(2).strip()

            # Extract the title (first line)
            lines = entry_text.split("\n")
            title = lines[0].strip()

            # Extract URL, DOI, and other metadata from subsequent lines
            url = ""
            metadata = {}
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("URL:"):
                    url = line[4:].strip()
                elif line.startswith("DOI:"):
                    metadata["doi"] = line[4:].strip()
                elif line.startswith("Published in"):
                    metadata["journal"] = line[12:].strip()
                # Add more metadata parsing as needed
                elif line:
                    # Store other lines as additional metadata
                    if "additional" not in metadata:
                        metadata["additional"] = []
                    additional = metadata["additional"]
                    if isinstance(additional, list):
                        additional.append(line)

            # Combine title with additional metadata lines for full context
            full_text = entry_text

            # Create a unique key to avoid duplicates
            ref_key = (citation_num, title, url)
            if ref_key not in seen_refs:
                seen_refs.add(ref_key)
                # Create RIS entry with full text for metadata extraction
                ris_entry = self._create_ris_entry(
                    citation_num, full_text, url, metadata
                )
                ris_entries.append(ris_entry)

        return "\n".join(ris_entries)

    def _create_ris_entry(
        self,
        ref_id: str,
        full_text: str,
        url: str = "",
        metadata: dict | None = None,
    ) -> str:
        """Create a single RIS entry."""
        lines = []

        # Parse metadata from full text
        import re

        if metadata is None:
            metadata = {}

        # Extract title from first line
        lines = full_text.split("\n")
        title = lines[0].strip()

        # Extract year from full text (looks for 4-digit year)
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", full_text)
        year = year_match.group(1) if year_match else None

        # Extract authors if present (looks for "by Author1, Author2")
        authors_match = re.search(
            r"\bby\s+([^.\n]+?)(?:\.|\n|$)", full_text, re.IGNORECASE
        )
        authors = []
        if authors_match:
            authors_text = authors_match.group(1)
            # Split by 'and' or ','
            author_parts = re.split(r"\s*(?:,|\sand\s|&)\s*", authors_text)
            authors = [a.strip() for a in author_parts if a.strip()]

        # Extract DOI from metadata or text
        doi = metadata.get("doi")
        if not doi:
            doi_match = re.search(
                r"DOI:\s*([^\s\n]+)", full_text, re.IGNORECASE
            )
            doi = doi_match.group(1) if doi_match else None

        # Clean title - remove author and metadata info for cleaner title
        clean_title = title
        if authors_match and authors_match.start() < len(title):
            clean_title = (
                title[: authors_match.start()] + title[authors_match.end() :]
                if authors_match.end() < len(title)
                else title[: authors_match.start()]
            )
        clean_title = re.sub(
            r"\s*DOI:\s*[^\s]+", "", clean_title, flags=re.IGNORECASE
        )
        clean_title = re.sub(
            r"\s*Published in.*", "", clean_title, flags=re.IGNORECASE
        )
        clean_title = re.sub(
            r"\s*Volume.*", "", clean_title, flags=re.IGNORECASE
        )
        clean_title = re.sub(
            r"\s*Pages.*", "", clean_title, flags=re.IGNORECASE
        )
        clean_title = clean_title.strip()

        # TY - Type of reference (ELEC for electronic source/website)
        lines.append("TY  - ELEC")

        # ID - Reference ID
        lines.append(f"ID  - ref{ref_id}")

        # TI - Title
        lines.append(f"TI  - {clean_title if clean_title else title}")

        # AU - Authors
        for author in authors:
            lines.append(f"AU  - {author}")

        # DO - DOI
        if doi:
            lines.append(f"DO  - {doi}")

        # PY - Publication year (if found in title)
        if year:
            lines.append(f"PY  - {year}")

        # UR - URL
        if url:
            lines.append(f"UR  - {url}")

            # Try to extract domain as publisher
            try:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                domain = parsed.netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                # Extract readable publisher name from domain
                if domain == "github.com" or domain.endswith(".github.com"):
                    lines.append("PB  - GitHub")
                elif domain == "arxiv.org" or domain.endswith(".arxiv.org"):
                    lines.append("PB  - arXiv")
                elif domain == "reddit.com" or domain.endswith(".reddit.com"):
                    lines.append("PB  - Reddit")
                elif (
                    domain == "youtube.com"
                    or domain == "m.youtube.com"
                    or domain.endswith(".youtube.com")
                ):
                    lines.append("PB  - YouTube")
                elif domain == "medium.com" or domain.endswith(".medium.com"):
                    lines.append("PB  - Medium")
                elif domain == "pypi.org" or domain.endswith(".pypi.org"):
                    lines.append("PB  - Python Package Index (PyPI)")
                else:
                    # Use domain as publisher
                    lines.append(f"PB  - {domain}")
            except (ValueError, AttributeError):
                pass

        # Y1 - Year accessed (current year)
        from datetime import datetime, UTC

        current_year = datetime.now(UTC).year
        lines.append(f"Y1  - {current_year}")

        # DA - Date accessed
        current_date = datetime.now(UTC).strftime("%Y/%m/%d")
        lines.append(f"DA  - {current_date}")

        # LA - Language
        lines.append("LA  - en")

        # ER - End of reference
        lines.append("ER  - ")

        return "\n".join(lines)


class LaTeXExporter:
    """Export markdown documents to LaTeX format."""

    def __init__(self):
        # Also match Unicode lenticular brackets 【】 (U+3010 and U+3011) that LLMs sometimes generate
        self.citation_pattern = re.compile(r"[\[【](\d+)[\]】]")
        self.heading_patterns = [
            (re.compile(r"^# (.+)$", re.MULTILINE), r"\\section{\1}"),
            (re.compile(r"^## (.+)$", re.MULTILINE), r"\\subsection{\1}"),
            (re.compile(r"^### (.+)$", re.MULTILINE), r"\\subsubsection{\1}"),
        ]
        self.emphasis_patterns = [
            (re.compile(r"\*\*(.+?)\*\*"), r"\\textbf{\1}"),
            (re.compile(r"\*(.+?)\*"), r"\\textit{\1}"),
            (re.compile(r"`(.+?)`"), r"\\texttt{\1}"),
        ]

    def export_to_latex(self, content: str) -> str:
        """
        Convert markdown document to LaTeX format.

        Args:
            content: Markdown content

        Returns:
            LaTeX formatted content
        """
        latex_content = self._create_latex_header()

        # Convert markdown to LaTeX
        body_content = content

        # Escape special LaTeX characters but preserve math mode
        # Split by $ to preserve math sections
        parts = body_content.split("$")
        for i in range(len(parts)):
            # Even indices are outside math mode
            if i % 2 == 0:
                # Only escape if not inside $$
                if not (
                    i > 0
                    and parts[i - 1] == ""
                    and i < len(parts) - 1
                    and parts[i + 1] == ""
                ):
                    # Preserve certain patterns that will be processed later
                    # like headings (#), emphasis (*), and citations ([n])
                    lines = parts[i].split("\n")
                    for j, line in enumerate(lines):
                        # Don't escape lines that start with # (headings)
                        if not line.strip().startswith("#"):
                            # Don't escape emphasis markers or citations for now
                            # They'll be handled by their own patterns
                            temp_line = line
                            # Escape special chars except *, #, [, ]
                            temp_line = temp_line.replace("&", r"\&")
                            temp_line = temp_line.replace("%", r"\%")
                            temp_line = temp_line.replace("_", r"\_")
                            # Don't escape { } inside citations
                            lines[j] = temp_line
                    parts[i] = "\n".join(lines)
        body_content = "$".join(parts)

        # Convert headings
        for pattern, replacement in self.heading_patterns:
            body_content = pattern.sub(replacement, body_content)

        # Convert emphasis
        for pattern, replacement in self.emphasis_patterns:
            body_content = pattern.sub(replacement, body_content)

        # Convert citations to LaTeX \cite{} format
        body_content = self.citation_pattern.sub(r"\\cite{\1}", body_content)

        # Convert lists
        body_content = self._convert_lists(body_content)

        # Add body content
        latex_content += body_content

        # Add bibliography section
        latex_content += self._create_bibliography(content)

        # Add footer
        latex_content += self._create_latex_footer()

        return latex_content

    def _create_latex_header(self) -> str:
        """Create LaTeX document header."""
        return r"""\documentclass[12pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{hyperref}
\usepackage{cite}
\usepackage{url}

\title{Research Report}
\date{\today}

\begin{document}
\maketitle

"""

    def _create_latex_footer(self) -> str:
        """Create LaTeX document footer."""
        return "\n\\end{document}\n"

    def _escape_latex(self, text: str) -> str:
        """Escape special LaTeX characters in text."""
        # Escape special LaTeX characters
        replacements = [
            ("\\", r"\textbackslash{}"),  # Must be first
            ("&", r"\&"),
            ("%", r"\%"),
            ("$", r"\$"),
            ("#", r"\#"),
            ("_", r"\_"),
            ("{", r"\{"),
            ("}", r"\}"),
            ("~", r"\textasciitilde{}"),
            ("^", r"\textasciicircum{}"),
        ]

        for old, new in replacements:
            text = text.replace(old, new)

        return text

    def _convert_lists(self, content: str) -> str:
        """Convert markdown lists to LaTeX format."""
        # Simple conversion for bullet points
        content = re.sub(r"^- (.+)$", r"\\item \1", content, flags=re.MULTILINE)

        # Add itemize environment around list items
        lines = content.split("\n")
        result = []
        in_list = False

        for line in lines:
            if line.strip().startswith("\\item"):
                if not in_list:
                    result.append("\\begin{itemize}")
                    in_list = True
                result.append(line)
            else:
                if in_list and line.strip():
                    result.append("\\end{itemize}")
                    in_list = False
                result.append(line)

        if in_list:
            result.append("\\end{itemize}")

        return "\n".join(result)

    def _create_bibliography(self, content: str) -> str:
        """Extract sources and create LaTeX bibliography."""
        sources_start = find_sources_section(content)
        if sources_start == -1:
            return ""

        sources_content = content[sources_start:]
        pattern = re.compile(
            r"^\[(\d+)\]\s*(.+?)(?:\n\s*URL:\s*(.+?))?$", re.MULTILINE
        )

        bibliography = "\n\\begin{thebibliography}{99}\n"

        for match in pattern.finditer(sources_content):
            citation_num = match.group(1)
            title = match.group(2).strip()
            url = match.group(3).strip() if match.group(3) else ""

            # Escape special LaTeX characters in title
            escaped_title = self._escape_latex(title)

            if url:
                bibliography += f"\\bibitem{{{citation_num}}} {escaped_title}. \\url{{{url}}}\n"
            else:
                bibliography += (
                    f"\\bibitem{{{citation_num}}} {escaped_title}.\n"
                )

        bibliography += "\\end{thebibliography}\n"

        return bibliography
