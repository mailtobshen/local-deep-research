"""Sanitize the References block to drop entries the body never cites."""
from __future__ import annotations

import re

from loguru import logger

# References-block location: mirror _scan_references_block's lookup so we
# find the SAME block start it parses. find_sources_section lives in the
# citation_formatter; the CJK-heading fallback uses _HEADING_RE +
# _SKIPPED_SECTION_HEADINGS from relevance.
from local_deep_research.text_optimization.citation_formatter import (
    find_sources_section,
)
from .relevance import _HEADING_RE, _SKIPPED_SECTION_HEADINGS


def _find_references_start(markdown: str) -> int:
    """Return the absolute offset where the References block begins, or -1.

    Identical logic to the top of ``_scan_references_block``: try the
    English/sources detector first, then fall back to CJK headings.
    """
    start = find_sources_section(markdown)
    if start < 0:
        for m in _HEADING_RE.finditer(markdown):
            if m.group(2).strip().lower() in _SKIPPED_SECTION_HEADINGS:
                start = m.start()
                break
    return start


def _used_nums_in_body(markdown: str, refs_start: int) -> set[str]:
    """Return the set of citation numbers appearing before the References block.

    Matches both production ``[[N]](url)`` markdown-link citations and
    plain ``[N]`` / ``[2, 3]`` bracket citations (fixture/legacy shape).
    """
    body = markdown[:refs_start]
    nums: set[str] = set()
    # ASCII [[N]](url) / [N] / [2, 3] plus the full-width 【N】 form
    # (CITE_INLINE_RE accepts it, so the citation index's section scan
    # treats it as a citation — the sanitizer must agree).
    for m in re.finditer(r"\[\[?([\d,\s]+)\]\]?|【([\d,\s]+)】", body):
        g = m.group(1) if m.group(1) is not None else m.group(2)
        for n in g.split(","):
            n = n.strip()
            if n.isdigit():
                nums.add(n)
    return nums


def sanitize_references(markdown: str) -> str:
    """Remove References-block rows whose [[N]] is not cited in the body.

    Preserves the original numbering of kept rows (no renumbering). If
    there is no References/Sources/参考文献 heading, the markdown is
    returned unchanged.
    """
    if not markdown:
        return markdown
    start = _find_references_start(markdown)
    if start < 0:
        return markdown

    used = _used_nums_in_body(markdown, start)
    refs_block = markdown[start:]

    # Each row begins with [N...] or [[N...]] at line start. Split into
    # row chunks. Production rows are single-bracket comma groups
    # ("[1, 1224] Title (source nr: 1, 1224)" — format_links_to_markdown
    # output); the leading bracket must NOT match non-numeric lines
    # like "[text](url)" links inside the block.
    row_starts = [m.start() for m in re.finditer(r"(?m)^\[\[?[\d,\s]+\]", refs_block)]
    if not row_starts:
        return markdown

    # Preserve the header section before the first reference row
    header = refs_block[: row_starts[0]] if row_starts else ""
    kept_chunks: list[str] = [header]

    for i, rs in enumerate(row_starts):
        re_end = row_starts[i + 1] if i + 1 < len(row_starts) else len(refs_block)
        chunk = refs_block[rs:re_end]
        # The leading bracket carries the citation number(s) for this
        # row. Digits later in the line (years, day counts, usernames)
        # are title text, not citations — e.g. "[138, ...] GitHub -
        # 0xk1h0/ChatGPT_DAN" contains "1" but 1 is not cited by this
        # row. Only the leading bracket digits count.
        nl = chunk.find("\n")
        head = chunk[:nl] if nl != -1 else chunk
        head_match = re.match(r"^(\[\[?)([\d,\s]+)(\]\]?)", head)
        if not head_match:
            continue
        row_nums_list = [n.strip() for n in head_match.group(2).split(",")]
        row_nums = set(row_nums_list)
        if not (row_nums & used):
            continue
        if row_nums - used:
            # Comma-group row with uncited members: every member of a
            # production row shares ONE URL (format_links_to_markdown
            # groups citations by canonical URL), so dropping the
            # uncited members from the leading bracket breaks no URL
            # mapping. The "(source nr: ...)" suffix is a verbatim
            # echo of the bracket — sync it when it matches exactly,
            # and leave the title untouched otherwise (LLM-written
            # rows may contain similar-looking text that is not an
            # echo).
            kept_nums = [n for n in row_nums_list if n in used]
            new_head = (
                head_match.group(1)
                + ", ".join(kept_nums)
                + head_match.group(3)
                + head[head_match.end():]
            )
            paren = re.search(r"\((source nr: )([\d,\s]+)\)$", new_head)
            if paren and paren.group(2).strip() == ", ".join(row_nums_list):
                new_head = (
                    new_head[: paren.start()]
                    + f"({paren.group(1)}{', '.join(kept_nums)})"
                )
            chunk = new_head + chunk[nl:] if nl != -1 else new_head
        kept_chunks.append(chunk)

    # kept_chunks includes header + kept rows; subtract header to count only rows
    kept_rows_count = len(kept_chunks) - 1
    logger.info(
        f"[IMG-TRACE] REFERENCES_CLEANED "
        f"before={len(row_starts)} after={kept_rows_count}"
    )
    return markdown[:start] + "".join(kept_chunks)
