# Citation-Anchored Image Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `enhance_report_with_images` to drive image selection purely from in-text `[[N]]` citation numbers, eliminating LLM citation hallucination in References and the LLM image-position guesser.

**Architecture:** In-place rewrite of the function body (entry signature unchanged). Four stages: build a citation index (num→url, section→nums, url→html), sanitize the References block to drop uncited entries, extract `<img>` only from cited sources with a single-section semantic gate, then insert images deterministically at the citation's section. No refetch, no new persistence, no DB migration — image source data (`results["findings"][].search_results[].html_content`) flows in-process.

**Tech Stack:** Python 3.12, pytest, loguru (IMG-TRACE), sentence-transformers `paraphrase-multilingual-mpnet-base-v2` (lazy-loaded), existing in-repo helpers (`_split_sections`, `loads_images`, `_dedupe_images`, `_safe_alt`).

**Spec:** `docs/superpowers/specs/2026-08-02-citation-anchored-image-pipeline-design.md`

## Global Constraints

- **Commit workflow:** `main` is the only active branch. Before every commit run `git rev-parse --abbrev-ref HEAD`; if it does not print `main`, STOP. No background git ops. After every commit run `git log --oneline -3` and confirm the new commit is HEAD while on `main`.
- **TDD:** Every task writes the failing test first, runs it to confirm it fails for the right reason, implements minimal code, runs to confirm pass, then commits.
- **Surgical changes:** Touch only what each task requires. Match existing style (loguru logger, type hints, docstrings). Do not refactor adjacent code.
- **Test isolation:** Tests must not require network, the HF model download, or a live DB. Mock the HF model with the `_fake_model` pattern where semantic embedding is needed; use plain strings elsewhere.
- **Reuse, don't rewrite:** These existing helpers MUST be reused unchanged — `extract_images`/`ExtractedImage` (`images/extractor.py`), `loads_images` (`images/serialize.py`), `ImageBank` (`images/bank.py`), `_scan_references_block`/`_split_sections`/`_section_offsets` (`images/relevance.py`), `build_report_entity_pool`/`_canonical_section_phrase`/`_encode_phrase_cached`/`_cosine`/`get_model` (`images/semantic_matcher.py`), `_dedupe_images`/`_safe_alt` (`images/postprocessing.py`), `ImageStore` (`images/store.py`).
- **Citation regexes** `CITE_INLINE_RE` / `CITE_INLINE_GROUP_RE` are defined in `text_optimization/citation_formatter.py` and already imported into `relevance.py`.
- **Pause, don't delete:** `ImageEnhancer` (`images/enhancer.py`) and `semantic_match_filter` (`images/semantic_matcher.py`) are paused (calls bypassed, code retained), NOT removed.
- **Run tests with:** `.venv/bin/pytest` (project venv). Lint with `.venv/bin/ruff check`.
- **Three checks already paused** (commit `1df26062`): inherited orphan-URL inheritance, ambiguous_match, no_source_url_match. This plan builds on top of that state; do not re-enable them.

---

## File Structure

**New files:**
- `src/local_deep_research/images/reference_sanitizer.py` — `sanitize_references(markdown) -> str` (stage 1, drop uncited References rows). Pure function, no deps on the image modules.
- `tests/images/test_reference_sanitizer.py`
- `tests/images/test_citation_index.py`
- `tests/images/test_insert_images.py`

**Modified files:**
- `src/local_deep_research/images/relevance.py` — add `build_citation_index(markdown, results)` (stage 0).
- `src/local_deep_research/images/postprocessing.py` — add `insert_images_by_section(markdown, placements)` (stage 3); rewrite `enhance_report_with_images` body to the 4-stage citation-anchored flow; bypass the `ImageEnhancer` call (pause).
- `src/local_deep_research/images/enhancer.py` — add a paused-marker comment at the top of the module (no code removal).

**Untouched (reused):** `extractor.py`, `serialize.py`, `bank.py`, `store.py`, `semantic_matcher.py` (functions reused; `semantic_match_filter` left in place but no longer called by the main flow).

---

## Task 1: References sanitizer (stage 1)

**Files:**
- Create: `src/local_deep_research/images/reference_sanitizer.py`
- Test: `tests/images/test_reference_sanitizer.py`

**Interfaces:**
- Consumes: `_scan_references_block` from `images.relevance` (it internally locates the References block via `find_sources_section` + a CJK-heading fallback — reuse this exact same location logic rather than re-deriving it). Also `find_sources_section` from `text_optimization.citation_formatter` and `_HEADING_RE` / `_SKIPPED_SECTION_HEADINGS` from `images.relevance`.
- Produces: `sanitize_references(markdown: str) -> str`.

- [x] **Step 1: Write the failing test**

Create `tests/images/test_reference_sanitizer.py`:

```python
from local_deep_research.images.reference_sanitizer import sanitize_references


def _md(used_nums: list[int], all_nums: list[int]) -> str:
    """Build a markdown with a body citing `used_nums` and a References block listing `all_nums`."""
    body_cites = "".join(f"[[{n}]]" for n in used_nums)
    refs = "".join(
        f"[[{n}]] Title {n}\n   URL: https://example.com/{n}\n" for n in all_nums
    )
    return f"## Section\n\nText {body_cites}.\n\n## 参考文献\n\n{refs}"


def test_drops_uncited_reference_rows():
    """Rows whose number is not cited in the body are removed."""
    md = _md(used_nums=[1, 3], all_nums=list(range(1, 6)))  # body cites [1],[3]
    out = sanitize_references(md)
    assert "[[1]] Title 1" in out
    assert "[[3]] Title 3" in out
    assert "Title 2" not in out
    assert "Title 4" not in out
    assert "Title 5" not in out


def test_preserves_original_numbering():
    """Cited numbers keep their original value (no renumbering)."""
    md = _md(used_nums=[7], all_nums=[1, 7, 9])
    out = sanitize_references(md)
    assert "[[7]] Title 7" in out
    assert "[[1]] Title 1" not in out
    assert "[[9]] Title 9" not in out


def test_no_references_block_returns_unchanged():
    """No 参考文献/References heading -> markdown returned verbatim."""
    md = "## Section\n\nBody [[1]] with no references block.\n"
    assert sanitize_references(md) == md


def test_body_num_without_reference_row_is_silently_dropped():
    """A body [[N]] whose N has no References row just yields no row for it."""
    md = _md(used_nums=[1, 99], all_nums=[1])  # [99] cited but absent from refs
    out = sanitize_references(md)
    assert "[[1]] Title 1" in out
    # No crash; [99] simply has no row to keep or drop.
    assert "99" not in out.split("参考文献")[-1]
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/images/test_reference_sanitizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'local_deep_research.images.reference_sanitizer'`

- [x] **Step 3: Write minimal implementation**

Create `src/local_deep_research/images/reference_sanitizer.py`:

```python
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
    """Return the set of [[N]] numbers appearing before the References block."""
    body = markdown[:refs_start]
    return set(re.findall(r"\[\[(\d+)\]\]", body))


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

    # Each row begins with [[N...]] at line start. Split into row chunks.
    row_starts = [m.start() for m in re.finditer(r"(?m)^\[\[", refs_block)]
    if not row_starts:
        return markdown

    kept_chunks: list[str] = []
    for i, rs in enumerate(row_starts):
        re_end = row_starts[i + 1] if i + 1 < len(row_starts) else len(refs_block)
        chunk = refs_block[rs:re_end]
        # The leading line carries the citation number(s) for this row.
        head = chunk[: chunk.find("\n")]
        row_nums = set(re.findall(r"\d+", head))
        if row_nums & used:
            kept_chunks.append(chunk)

    logger.info(
        f"[IMG-TRACE] REFERENCES_CLEANED "
        f"before={len(row_starts)} after={len(kept_chunks)}"
    )
    return markdown[:start] + "".join(kept_chunks)
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/images/test_reference_sanitizer.py -v`
Expected: PASS (all 4 tests).

The four tests are the contract. If a symbol import fails, confirm it with `grep -n "_HEADING_RE\|_SKIPPED_SECTION_HEADINGS" src/local_deep_research/images/relevance.py` and `grep -n "def find_sources_section" src/local_deep_research/text_optimization/citation_formatter.py` — all three are verified present at the modules named above.

- [x] **Step 5: Commit** — Actual: `987adb38` (+ edge-case fixes `3798ddcf`)

```bash
git rev-parse --abbrev-ref HEAD   # must print main
git add src/local_deep_research/images/reference_sanitizer.py tests/images/test_reference_sanitizer.py
git commit -m "images: add reference_sanitizer to drop uncited References rows

Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -3
```

---

## Task 2: Citation index (stage 0)

**Files:**
- Modify: `src/local_deep_research/images/relevance.py` (add `build_citation_index`)
- Test: `tests/images/test_citation_index.py`

**Interfaces:**
- Consumes: `_scan_references_block(markdown)`, `_split_sections(markdown)`, `_section_offsets(markdown)`, `CITE_INLINE_RE`/`CITE_INLINE_GROUP_RE` (already imported in relevance.py from `text_optimization.citation_formatter`).
- Produces: `build_citation_index(markdown: str, results: dict) -> tuple[dict[str,str], dict[int,list[str]], dict[str,str]]` returning `(num_to_url, section_to_nums, url_to_html)`.

- [x] **Step 1: Write the failing test**

Create `tests/images/test_citation_index.py`:

```python
from local_deep_research.images.relevance import build_citation_index


def test_builds_three_mappings():
    md = (
        "## A\n\nText [[1]] here.\n\n"
        "## B\n\nNo citation.\n\n"
        "## 参考文献\n\n"
        "[[1]] Source A\n   URL: https://example.com/a\n"
    )
    results = {
        "findings": [
            {"search_results": [
                {"url": "https://example.com/a", "html_content": "[]"},
                {"url": "https://example.com/other", "html_content": "[]"},
            ]}
        ]
    }
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, results)

    assert num_to_url == {"1": "https://example.com/a"}
    # Section 0 (## A) cites [1]; section 1 (## B) is orphan -> [].
    assert section_to_nums[0] == ["1"]
    assert section_to_nums[1] == []
    assert url_to_html["https://example.com/a"] == "[]"
    assert url_to_html["https://example.com/other"] == "[]"


def test_html_mapping_omits_search_results_without_html_content():
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[[1]] S\n   URL: https://x/a\n"
    results = {"findings": [{"search_results": [
        {"url": "https://x/a"},  # no html_content key
    ]}]}
    _, _, url_to_html = build_citation_index(md, results)
    assert url_to_html == {}  # missing html_content -> not indexed


def test_empty_results_yields_empty_html_map():
    md = "## A\n\n[[1]].\n\n## 参考文献\n\n[[1]] S\n   URL: https://x/a\n"
    num_to_url, section_to_nums, url_to_html = build_citation_index(md, {"findings": []})
    assert num_to_url == {"1": "https://x/a"}
    assert url_to_html == {}
    assert section_to_nums[0] == ["1"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/images/test_citation_index.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_citation_index'`

- [x] **Step 3: Write minimal implementation**

Add to `src/local_deep_research/images/relevance.py` (after `extract_segment_sources`):

```python
def build_citation_index(
    markdown: str,
    results: dict,
) -> tuple[dict[str, str], dict[int, list[str]], dict[str, str]]:
    """Build the three lookup tables the citation-anchored pipeline uses.

    Returns ``(num_to_url, section_to_nums, url_to_html)``:

    * ``num_to_url``: ``{citation_number_str: source_url}`` from the
      References block (via ``_scan_references_block``).
    * ``section_to_nums``: ``{section_idx: [num_str, ...]}`` — the
      ``[[N]]`` markers each section's body actually cites. Sections
      with no marker map to ``[]`` (orphans get no images).
    * ``url_to_html``: ``{source_url: html_content}`` from
      ``results["findings"][].search_results[]`` — the real fetched
      content images are extracted from. Search results without an
      ``html_content`` value are omitted.

    Stage 0 only assembles mappings; it makes no keep/drop decisions.
    """
    num_to_url: dict[str, str] = dict(_scan_references_block(markdown))

    sections = _split_sections(markdown)
    offsets = _section_offsets(markdown)
    section_to_nums: dict[int, list[str]] = {}
    for idx in range(len(sections)):
        body_start = offsets[idx] if idx < len(offsets) else 0
        body_end = (
            offsets[idx + 1] if idx + 1 < len(offsets) else len(markdown)
        )
        body_slice = markdown[body_start:body_end]
        nums: list[str] = []
        seen: set[str] = set()
        for m in CITE_INLINE_RE.finditer(body_slice):
            n = m.group(1)
            if n not in seen:
                seen.add(n)
                nums.append(n)
        for m in CITE_INLINE_GROUP_RE.finditer(body_slice):
            for n in m.group(1).split(","):
                n = n.strip()
                if n and n not in seen:
                    seen.add(n)
                    nums.append(n)
        section_to_nums[idx] = nums

    url_to_html: dict[str, str] = {}
    for finding in results.get("findings", []) or []:
        for sr in finding.get("search_results", []) or []:
            url = sr.get("url")
            html = sr.get("html_content")
            if url and html and url not in url_to_html:
                url_to_html[url] = html

    return num_to_url, section_to_nums, url_to_html
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/images/test_citation_index.py -v`
Expected: PASS (all 3 tests).

- [x] **Step 5: Commit** — Actual: `dfde7308` (+ production `link`-key + `[[N]]` citation fix `323851cb`)

```bash
git rev-parse --abbrev-ref HEAD   # must print main
git add src/local_deep_research/images/relevance.py tests/images/test_citation_index.py
git commit -m "images: add build_citation_index for the citation-anchored pipeline

Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -3
```

---

## Task 3: Deterministic image insertion (stage 3)

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` (add `insert_images_by_section`)
- Test: `tests/images/test_insert_images.py`

**Interfaces:**
- Consumes: `_split_sections`/`_section_offsets` from `images.relevance`; `_safe_alt` (same module).
- Produces: `insert_images_by_section(markdown: str, placements: list[tuple[int, str, str]]) -> str`. Each placement is `(section_idx, url, alt)`; the list is sorted by `section_idx` by the caller.

- [x] **Step 1: Write the failing test**

Create `tests/images/test_insert_images.py`:

```python
from local_deep_research.images.postprocessing import insert_images_by_section


def test_inserts_image_after_section_heading():
    md = "## A\n\nBody text.\n\n## B\n\nMore text.\n"
    out = insert_images_by_section(
        md, [(0, "https://x/a.jpg", "Tower")]
    )
    assert "## A\n\n![Tower](https://x/a.jpg)" in out
    # Section B unchanged.
    assert "## B\n\nMore text." in out


def test_multiple_images_one_section_inserted_in_order():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(
        md,
        [(0, "https://x/1.jpg", "One"), (0, "https://x/2.jpg", "Two")],
    )
    assert out.index("https://x/1.jpg") < out.index("https://x/2.jpg")
    assert "![One](https://x/1.jpg)" in out
    assert "![Two](https://x/2.jpg)" in out


def test_empty_alt_skipped():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(md, [(0, "https://x/a.jpg", "")])
    assert "https://x/a.jpg" not in out


def test_section_idx_out_of_range_skipped():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(md, [(5, "https://x/a.jpg", "Tower")])
    assert out == md


def test_sanitizes_alt_via_safe_alt():
    """alt with brackets/newlines is cleaned before insertion.

    _safe_alt('hello [world]\\nfoo') == 'hello world foo' (strips [ ],
    collapses whitespace). Verified: the rendered markdown carries the
    cleaned alt, not the raw one.
    """
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(
        md, [(0, "https://x/a.jpg", "hello [world]\nfoo")]
    )
    assert "![hello world foo](https://x/a.jpg)" in out
    assert "[" not in out  # brackets stripped from the alt
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/images/test_insert_images.py -v`
Expected: FAIL with `ImportError: cannot import name 'insert_images_by_section'`

- [x] **Step 3: Write minimal implementation**

Add to `src/local_deep_research/images/postprocessing.py` (after `_safe_alt`):

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/images/test_insert_images.py -v`
Expected: PASS (all 5 tests).

`_safe_alt("hello [world]\nfoo")` returns `"hello world foo"` (verified: strips `[`/`]`, collapses whitespace). The assertions above match that output.

- [x] **Step 5: Commit** — Actual: `50279797` (+ test fix `0d628c69`)

```bash
git rev-parse --abbrev-ref HEAD   # must print main
git add src/local_deep_research/images/postprocessing.py tests/images/test_insert_images.py
git commit -m "images: add insert_images_by_section for deterministic citation placement

Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -3
```

---

## Task 4: Pause marker on ImageEnhancer

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py` (add paused-marker comment at top of module)
- No test (comment-only change; verified by the rewrite in Task 5 not calling it).

**Interfaces:**
- Consumes: nothing.
- Produces: a visible marker so the pause is discoverable.

- [x] **Step 1: Read the top of enhancer.py**

Run: `.venv/bin/python -c "print(open('src/local_deep_research/images/enhancer.py').read()[:400])"`
Note the current module docstring (if any) so the marker is inserted cleanly above `ImageEnhancer`.

- [x] **Step 2: Add the paused marker**

Insert at the very top of `src/local_deep_research/images/enhancer.py` (before any existing docstring is fine — above the first class/import line as a module-level comment block):

```python
# PAUSED (2026-08-02): ImageEnhancer is no longer invoked by the
# citation-anchored image pipeline (enhance_report_with_images). Image
# placement is now deterministic — driven by the citation number's
# section — so the LLM position-guesser is bypassed. The class and its
# imports are retained pending confirmation that the new pipeline is
# stable; removal is a separate change. Do not add new callers.
```

- [x] **Step 3: Verify the module still imports cleanly**

Run: `.venv/bin/python -c "from local_deep_research.images.enhancer import ImageEnhancer; print('ok')"`
Expected: prints `ok` (no SyntaxError from the comment).

- [x] **Step 4: Commit** — Actual: `d020307a`

```bash
git rev-parse --abbrev-ref HEAD   # must print main
git add src/local_deep_research/images/enhancer.py
git commit -m "images: mark ImageEnhancer as paused for citation-anchored pipeline

Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -3
```

---

## Task 5: Rewrite enhance_report_with_images to the 4-stage citation-anchored flow

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` (rewrite the body of `enhance_report_with_images`, lines ~96-549 — keep the signature and the outer try/except shell)
- Test: `tests/images/test_postprocessing_citation_pipeline.py` (new — end-to-end with mocked HF model)

**Interfaces:**
- Consumes (from earlier tasks): `sanitize_references` (Task 1), `build_citation_index` (Task 2), `insert_images_by_section` (Task 3). Plus reused helpers: `loads_images`, `ImageBank`, `build_report_entity_pool`, `_canonical_section_phrase`, `_encode_phrase_cached`, `_cosine`, `get_model`, `_dedupe_images`, `ImageStore`.
- Produces: a rewritten `enhance_report_with_images(*, research_id, clean_markdown, results, db_session, enable_images, vision_model, ...) -> str` with the same signature and the same outer error-handling contract (any failure → return clean_markdown).

This is the largest task. It is one task because the four stages only make sense wired together — a reviewer gate on the integrated flow is the meaningful unit.

- [x] **Step 1: Write the failing end-to-end test**

Create `tests/images/test_postprocessing_citation_pipeline.py`:

```python
"""End-to-end test of the citation-anchored image pipeline.

Uses a fake HF model so no download/network is required. The fake
returns a hand-picked vector per phrase; we arrange vectors so the
cited image's alt is highly similar to its section phrase and an
unrelated image is dissimilar.
"""
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing


def _fake_model(vectors: dict[str, list[float]]):
    """Return a fake model whose encode(phrase) -> vectors[phrase]."""

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [vectors.get(p, [0.0, 0.0, 0.0, 0.0]) for p in phrases]

    return _M()


def test_cited_image_passes_gate_and_is_inserted(monkeypatch):
    """An image whose alt matches its citation's section is inserted there."""
    md = (
        "## Canton Tower\n\nThe tower [[1]] is tall.\n\n"
        "## 参考文献\n\n"
        "[[1]] Canton Tower source\n   URL: https://src/page\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/page", "html_content": (
            '[{"url": "https://img/tower.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/page", "source_title": "ct", '
            '"width": null, "height": null}]'
        )},
    ]}]}

    fake = _fake_model({
        "Canton Tower": [1.0, 0.0, 0.0, 0.0],   # alt
        # section phrase = heading + entities; arrange same direction.
    })
    monkeypatch.setattr(postprocessing, "get_model", lambda *a, **k: fake)
    # build_report_entity_pool + _canonical_section_phrase run on the
    # real markdown; make the section phrase embed to the same vector
    # by having encode fall through to the default for unknown phrases
    # and patching _canonical_section_phrase to return "Canton Tower".
    monkeypatch.setattr(
        postprocessing, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )

    with patch.object(postprocessing, "ImageEnhancer") as enh_mock, \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {"https://img/tower.jpg": "/images/r/t.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        out = postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=md,
            results=results,
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    # The cited image is inserted into the Canton Tower section.
    assert "![Canton Tower](https://img/tower.jpg)" in out
    # ImageEnhancer must NOT have been called (it is paused).
    enh_mock.assert_not_called()


def test_uncited_source_image_not_extracted(monkeypatch):
    """An image whose source is never cited in the body is never even considered."""
    md = (
        "## Canton Tower\n\nThe tower [[1]].\n\n"
        "## 参考文献\n\n"
        "[[1]] Source\n   URL: https://src/cited\n"
    )
    results = {"findings": [{"search_results": [
        # Cited source has no images.
        {"url": "https://src/cited", "html_content": "[]"},
        # Uncited source HAS an image — must be ignored entirely.
        {"url": "https://src/uncited", "html_content": (
            '[{"url": "https://img/stray.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/uncited", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    with patch.object(postprocessing, "ImageEnhancer"), \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "stray.jpg" not in out


def test_low_similarity_image_dropped(monkeypatch):
    """An image whose alt is orthogonal to its section is dropped."""
    md = (
        "## Canton Tower\n\n[[1]].\n\n"
        "## 参考文献\n\n[[1]] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/x.jpg", "alt": "Banana", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({
        "Banana": [1.0, 0.0, 0.0, 0.0],          # alt direction
        # section phrase default -> [0,0,0,0], cosine 0 < threshold.
    })
    monkeypatch.setattr(postprocessing, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing, "_canonical_section_phrase",
        lambda heading, entities: "section phrase orthogonal",
    )
    with patch.object(postprocessing, "ImageEnhancer"), \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "Banana" not in out
    assert "img/x.jpg" not in out


def test_enable_images_false_returns_markdown_unchanged():
    out = postprocessing.enhance_report_with_images(
        research_id="r", clean_markdown="# hi", results={"findings": []},
        db_session=MagicMock(), enable_images=False, vision_model="",
    )
    assert out == "# hi"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/images/test_postprocessing_citation_pipeline.py -v`
Expected: FAIL (the current implementation calls ImageEnhancer / uses the old gate; `enh_mock.assert_not_called()` fails, or assertions about stray.jpg fail).

- [x] **Step 3: Rewrite enhance_report_with_images**

In `src/local_deep_research/images/postprocessing.py`, replace the **body** of `enhance_report_with_images` (keep the signature `def enhance_report_with_images(*, research_id, clean_markdown, results, db_session, enable_images, vision_model, vision_url=None, vision_api_key=None, vision_min_alt_count=None, vision_cap=None, firecrawl_client=None, alt_similarity_threshold=_DEFAULT_THRESHOLD, alt_similarity_min_margin=_DEFAULT_MIN_MARGIN) -> str` and the outer `try/except Exception` that returns `clean_markdown` on failure). The new body:

```python
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
        entity_pool = build_report_entity_pool(clean_markdown)
        section_phrases: dict[int, str] = {}
        for sidx, entities in entity_pool.items():
            if sidx >= len(sections) or not entities:
                continue
            phrase = _canonical_section_phrase(sections[sidx][0], entities)
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
        binding: dict[str, tuple[str, int]] = {}  # url -> (num, section_idx)

        # Stage 2: extract images from each cited source, single-section
        # semantic gate against the citation's section.
        for sidx, nums in section_to_nums.items():
            if not nums or sidx not in section_vecs:
                continue
            sec_vec = section_vecs[sidx]
            for num in nums:
                url = num_to_url.get(num)
                html = url_to_html.get(url) if url else None
                if not html:
                    continue
                imgs = loads_images(html)
                if not imgs:
                    continue
                kept = 0
                dropped_low = 0
                model = get_model()
                for img in imgs:
                    if not (img.alt and img.alt.strip()):
                        continue
                    raw = model.encode([img.alt], normalize_embeddings=True)[0]
                    alt_vec = list(raw.tolist()) if hasattr(raw, "tolist") else list(raw)
                    score = _cosine(alt_vec, sec_vec)
                    if score >= threshold:
                        bank.add([img])
                        binding[img.url] = (num, sidx)
                        kept += 1
                    else:
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

        logger.info(
            f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} "
            f"total={len(bank.all_urls())}"
        )

        # Stage 3: deterministic insert at each image's bound section.
        # ImageEnhancer is intentionally NOT called (paused).
        # Build placements from binding (url -> (num, section_idx)) joined
        # with the bank's images in one pass (avoid O(n^2) lookups).
        bank_by_url = {img.url: img for img in bank.candidates_with_alt()}
        placements = sorted(
            (
                (sidx, url, bank_by_url[url].alt)
                for url, (num, sidx) in binding.items()
                if url in bank_by_url
            ),
            key=lambda p: (p[0], p[1]),
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
        store = ImageStore(research_id=research_id, db_session=db_session)
        mapping = store.persist(chosen, url_to_alt, url_to_source)
        enhanced = store.rewrite_markdown(enhanced, mapping)
        logger.info(
            f"[IMG-TRACE] PERSIST research={research_id} chosen={len(chosen)}"
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
```

Notes for the implementer:
- `_split_sections`, `insert_images_by_section`, `loads_images`, `ImageBank`, `_dedupe_images`, `_IMG_RE`, `_safe_alt` are in this module or imported at the top. Add imports at the top of the file for `sanitize_references` (from `.reference_sanitizer`), `build_citation_index` (from `.relevance`), and `build_report_entity_pool`, `_canonical_section_phrase`, `_encode_phrase_cached`, `_cosine`, `get_model` (from `.semantic_matcher`). Remove now-unused imports (e.g. `ImageEnhancer`, `DEFAULT_VISION_CAP`, `extract_segment_sources`, `semantic_match_filter`) only if they are no longer referenced anywhere in the file — check with grep first.
- `ImageStore(research_id, db_session, base_dir=..., firecrawl_client=None)` and `persist(urls, url_to_alt=None, url_to_source=None)` — the call above uses the verified signatures. `rewrite_markdown(enhanced, mapping)` is unchanged.
- Keep `alt_similarity_min_margin` in the signature (callers may pass it) but it is unused in the new body — that is intentional (ambiguous_match is paused).

- [x] **Step 4: Run the new test to verify it passes**

Run: `.venv/bin/pytest tests/images/test_postprocessing_citation_pipeline.py -v`
Expected: PASS (all 4 tests).

- [x] **Step 5: Run the full image test suite to check for regressions**

Run: `.venv/bin/pytest tests/images/ -q --no-header -p no:cacheprovider`
Expected: The new tests pass. Some pre-existing tests that asserted the old gate behavior (e.g. in `test_postprocessing.py`) may now fail because the pipeline changed — for each failure, decide:
  - If the test asserted a behavior the new pipeline still honors (e.g. `enable_images=False` returns unchanged) → keep it.
  - If the test asserted the old LLM-enhancer / two-gate flow → update it to the new contract, or delete it if it is fully superseded by `test_postprocessing_citation_pipeline.py`.
Do NOT delete tests that still encode valid contracts. Ruff must stay clean on changed files: `.venv/bin/ruff check src/local_deep_research/images/postprocessing.py`.

- [x] **Step 6: Commit** — Actual: `500c9e2d` (+ import cleanup `1596b96f`; + fallback removal & link-key fix `f9b60e29`; tests `0c7dab73`)

```bash
git rev-parse --abbrev-ref HEAD   # must print main
git add src/local_deep_research/images/postprocessing.py tests/images/test_postprocessing_citation_pipeline.py
# plus any test files adjusted in Step 5
git commit -m "images: rewrite enhance_report_with_images to citation-anchored pipeline

Four stages: sanitize references, build citation index, extract images
from cited sources with a single-section semantic gate, insert
deterministically at the citation's section, dedupe. ImageEnhancer is
bypassed (paused); image placement no longer uses an LLM.

Co-Authored-By: Claude <noreply@anthropic.com>"
git log --oneline -3
```

---

## Task 6: B3 replay regression check

**Files:**
- No source changes. Uses the B3 replay harness already in the container at `/tmp/b3_replay.py` (from the diagnostic session) against research `4b97170e`.

**Interfaces:**
- Consumes: the rewritten `enhance_report_with_images` (Task 5).

This is a verification task, not a code task. It produces no commit; it produces a measured adoption number to compare against the baseline (0/97 original; ~19/97 after the three pauses).

- [x] **Step 1: Confirm the container has the rewritten code**

Run: `docker exec ldr-local sh -c 'grep -c "mode=citation_anchored" /install/.venv/lib/python3.14/site-packages/local_deep_research/images/postprocessing.py'`
Expected: prints `1` (the source is bind-mounted; the new code is live). If it prints `0`, the host edit was not synced — re-check the bind mount and re-copy if needed.

- [x] **Step 2: Run the B3 replay**

Run: `docker exec -u ldruser -e PYTHONUNBUFFERED=1 -e HF_HUB_OFFLINE=1 ldr-local python3 /tmp/b3_replay.py 2>/dev/null | grep -E "\[result\]|CITATION_MATCH|ELIGIBLE_BANK|INSERT|END "`
Expected: `[result] adoption rate:` is non-zero, and `INSERT placements=` > 0. The LLM `status=error` seen before MUST NOT recur (the new pipeline has no LLM call).

- [x] **Step 3: Record the result** (run 2026-08-02, after the "link"-key fix + fallback removal)

Real-model run (current code, threshold 0.6):

    CITATION_INDEX nums=2902 sections=184 html_covered=16
    CITATION_MATCH num=7  imgs=2 kept=0 low_similarity=2   (x2 sections)
    CITATION_MATCH num=10 imgs=1 kept=0 low_similarity=1
    CITATION_MATCH num=2406 imgs=4 kept=0 low_similarity=4
    ELIGIBLE_BANK total=0
    END status=empty
    [result] adoption rate: 0/97

Pipeline verdict on real data: correct. The index resolves all 2902
reference rows across 184 sections and covers all 16 search_results
via the production `"link"` key. Only 3 of the 16 candidate-bearing
sources are cited in BODY sections (the other 13 — porn sites,
telegram, MSI-laptop pages, Vietnamese AI blogs, ChatGPT jailbreak
repos — are cited only in the trailing References block and are
correctly never extracted). All 9 evaluated images carry degenerate
alts ("1", "3", "watch now", "longwriter") and the real semantic
model rejects every one — this is junk-alt rejection, not merely the
cross-language threshold. The 0/97 adoption is the pipeline doing its
job, not a regression.

Downstream-chain run (permissive constant-vector gate, same real
320K-char markdown — proves insert/dedupe/persist/rewrite on real
data):

    ELIGIBLE_BANK total=7
    INSERT placements=7
    PERSIST chosen=7
    END status=ok
    [result] adoption rate: 3/97  (3 = remote downloads that survived
    ImageStore.persist; rewrite_markdown drops URLs whose download
    failed — pre-existing network-dependent contract, not part of
    this plan)

The LLM `status=error` from the pre-pause baseline did NOT recur
(no LLM call exists in the new pipeline).

- [x] **Step 4: No commit**

This task verifies; it does not change code. Report the numbers in the session.

---

## Self-Review

**Spec coverage:**
- Stage 0 (citation index) → Task 2. ✓
- Stage 1 (References sanitize, goal 1A) → Task 1. ✓
- Stage 2 (citation-driven extract + single-section semantic gate, goal 2) → Task 5 body. ✓
- Stage 3 (deterministic insert, goal 3) → Task 3 (helper) + Task 5 (wiring). ✓
- Stage 4 (dedupe, goal 4) → Task 5 body (`_dedupe_images`). ✓
- Pause ImageEnhancer → Task 4 (marker) + Task 5 (bypass). ✓
- Error handling (any failure → clean_markdown) → Task 5 outer try/except. ✓
- B3 regression → Task 6. ✓
- Goal 1B (synthesis LLM constraint) → explicitly out of scope (spec "Non-goals" + "目标 1B 延后"). ✓ No task (correct).

**Placeholder scan:** Searched for TBD/TODO/"add error handling"/"similar to". None present. Every code step shows full code. The two spots flagged as "adjust if X" (Task 1 imports, Task 3 `_safe_alt` assertion, Task 5 `ImageStore` constructor) give the implementer a concrete decision rule, not a vague "handle it".

**Type consistency:** `build_citation_index` returns `(dict[str,str], dict[int,list[str]], dict[str,str])` in Task 2 and is consumed with that exact shape in Task 5. `insert_images_by_section` takes `list[tuple[int,str,str]]` in Task 3 and Task 5 builds exactly that. `binding` is `dict[str, tuple[str,int]]` (url→(num,section_idx)) consistent across Task 5. `placements` tuple order `(section_idx, url, alt)` matches Task 3's signature.

One correction applied during review: the spec located `build_report_entity_pool`/`_canonical_section_phrase` in relevance.py, but they are in `semantic_matcher.py` — Task 5's import note reflects the correct module.

---

## Post-Execution Review (2026-08-02)

Overall review (review agent + regression batch + real-data probes + manual
verification) found **4 fixable defects + 1 documented decision**:

| # | Finding | Verdict | Fix |
|---|---------|---------|-----|
| F-1 | Rewritten pipeline accepted-but-ignored `firecrawl_client` — anti-hotlink fallback lost | Fix | `ImageStore(..., firecrawl_client=firecrawl_client)` at construction |
| F-2 | `binding` last-write-wins: same source cited in 2+ sections overwrote the first binding | Fix | First-bound-section-wins (`if img.url not in binding` inside the gate) |
| F-3 | `_used_nums_in_body` missed full-width `【N】` citations (CITE_INLINE_RE accepts them) | Fix | Regex extended to `【([\d,\s]+)】` |
| F-4 | Sanitizer kept rows via digits later in the head line (years, usernames, day counts) — real B3 report: 190/230 rows kept solely by title digits | Fix | Only the leading `[N...]` bracket digits count as row numbers |
| F-5 | Comma-group rows `[1, 1224]` keep the whole row when one member is cited; dangling members (311 on real data) survive | **Not fixed** — documented decision; renumbering is a formatter-level concern (CITE_LIST_ROW_RE contract), out of scope for the sanitizer |

Informationals (all clean): row-boundary agreement bidirectional, placement
ordering correct, dedupe determinism + no dupes, MagicMock session parity OK,
no new lint debt.

**Verification:** 566 passed, 1 skipped (affected surface); ruff clean on all 4
changed files; real B3 report (research 4b97170e…): sanitizer 1831 → 40 rows
(was 230), output 320351 → 50963 bytes (−84%). Fix commit: (see git log).
