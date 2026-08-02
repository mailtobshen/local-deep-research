# Image Enhancer: Early Skip + Section-Title Weighting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop wasting ~15 LLM calls per report when a section's per-section candidate pool is empty, and let section-heading token matches count double in `extract_segment_sources()` so on-topic candidates that share the heading are not dropped under the current `score >= 1 and ratio >= 0.30` gate.

**Architecture:** Two surgical, behavior-preserving changes:

1. `ImageEnhancer.enhance()` in `src/local_deep_research/images/enhancer.py` — when `per_section_candidates` is provided and the per-section pool for an index is `[]`, skip the LLM call entirely, append the section markdown unchanged, and emit one `[IMG-TRACE] SECTION_SKIP` line. Applies to both the single-section path (lines 338-342) and the multi-section path (lines 343-355). The legacy full-pool path (`per_section_candidates is None`) is unchanged.
2. `extract_segment_sources()` in `src/local_deep_research/images/relevance.py` — split `section_terms` into `heading_terms | body_terms` and change the per-candidate score from `len(section_terms & cand_terms)` to `2 * len(heading_terms & cand_terms) + 1 * len(body_terms & cand_terms)`. The ratio denominator stays `len(cand_terms)`; the threshold `score >= 1 and ratio >= 0.30` is unchanged.

The public API of `ImageEnhancer.enhance(markdown, bank, per_section_candidates=None) -> str` is unchanged. The `extract_segment_sources(markdown, results, top_n=3) -> list[tuple[str, str, list[str]]]` signature is unchanged. The `from local_deep_research.images.postprocessing import extract_segment_sources` re-export used by `tests/images/test_segment_sources.py:12` continues to work.

**Tech Stack:** Python 3.12, loguru, pytest, `_match_terms` (existing CJK/Latin tokenizer in `relevance.py:318`). No new dependencies.

## Global Constraints

- All changes land on `main`; one task = one commit; no background git.
- Before every commit, run `git rev-parse --abbrev-ref HEAD` and confirm it prints `main`. After every commit, run `git log --oneline -3` and confirm the new commit is at HEAD with the intended message.
- Use the existing `feat(images): ...` commit style. Two commits: one for early-skip, one for heading-weighting.
- IMG-TRACE line format already in use throughout `images/`: bracket-prefixed, `key=value` pairs separated by spaces, no trailing period. Match that style.
- Do NOT touch `postprocessing.py` — the per-section candidate pool is already computed there and we receive it as a kwarg.
- Do NOT change `evaluate_candidate()` (relevance.py:781-955) or any other gate. Heading-weighting is scoped to `extract_segment_sources()` only.
- Do NOT change the `score >= 1 and ratio >= 0.30` threshold. The heading-weight boost sits *on top of* the existing rule.
- Do NOT change the inherited-allow-list fallback (relevance.py:408-409). An orphan section still inherits the previous section's allow-list.
- `_extract_registered_domain` boundary behavior is already covered by 10 existing tests in `test_per_section_domain_filter.py:24-99`; do not duplicate.

## Interface Contract (verified by tests after this plan lands)

```python
# No signature change. Behavior contract additions:

# 1. ImageEnhancer.enhance — per-section empty pool
#    in:  per_section_candidates = {0: []}
#    out: zero LLM calls, output == input, one [IMG-TRACE] SECTION_SKIP line
#    in:  per_section_candidates = {0: [img_a], 1: []}
#    out: exactly one LLM call (section 0); section 1 returned unchanged

# 2. ImageEnhancer.enhance — legacy full-pool path
#    in:  per_section_candidates is None
#    out: LLM called once per section with the full bank, unchanged

# 3. extract_segment_sources — heading-weighted score
#    in:  section heading = "广州塔", body short, candidate title = "广州塔珠江夜景"
#         (6 own tokens; 1 heading match, 0 body match)
#    out: score = 2*1 + 1*0 = 2, ratio = 2/6 ≈ 0.33, allowed
#    in:  same candidate with 100 dilution tokens
#    out: score = 2, ratio = 0.02, still dropped
#    in:  section heading generic, body has 1 weak token, candidate long
#    out: ratio < 0.30, dropped (regression for body-only matches)
```

---

### Task 1: Skip the LLM when a per-section pool is empty

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py:310-362` (the `enhance()` method, both single-section and multi-section paths)
- Modify: `tests/images/test_per_section_domain_filter.py:227-241` (replace `test_enhance_empty_per_section_pool_runs_but_no_images` with two new tests)

**Interfaces:**
- Consumes: `self.enhance(markdown, bank, per_section_candidates={0: []})` — already supported.
- Produces: when the per-section pool is `[]`, the LLM is **not** invoked; the section markdown is appended unchanged; one `[IMG-TRACE] SECTION_SKIP ...` line is logged at `INFO` level.

- [ ] **Step 1: Add the failing tests**

In `tests/images/test_per_section_domain_filter.py`, **replace** the existing `test_enhance_empty_per_section_pool_runs_but_no_images` function (lines 227-241) with the following two tests, in this order:

```python
def test_enhance_single_section_empty_pool_skips_llm():
    """A section with no candidates must not trigger an LLM call;
    the section markdown is returned unchanged. Saves ~2 s per
    empty section."""
    bank = ImageBank()
    bank.add([_img("https://a.com/x.jpg", "https://a.com/p")])

    llm = _CaptureLLM()
    per_section = {0: []}
    md = "# Solo\n\nbody"
    out = _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    assert llm.calls == []
    assert out == md


def test_enhance_multi_section_one_pool_empty_skips_that_section():
    """When only one of two per-section pools is empty, the LLM
    is called for the populated section only; the empty section is
    returned unchanged and the other section is enhanced."""
    img_a = _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/p")
    bank = ImageBank()
    bank.add([img_a])

    llm = _CaptureLLM()
    per_section = {0: [img_a], 1: []}
    md = "# Section A\n\nbody a\n\n## Section B\n\nbody b"
    out = _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    assert len(llm.calls) == 1
    assert "body a" in out
    assert "body b" in out
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/images/test_per_section_domain_filter.py::test_enhance_single_section_empty_pool_skips_llm tests/images/test_per_section_domain_filter.py::test_enhance_multi_section_one_pool_empty_skips_that_section -v`
Expected: BOTH FAIL — the current code calls `_run_enhance` for every section (including empty ones). The single-section test fails on `assert llm.calls == []`; the multi-section test fails on `assert len(llm.calls) == 1`.

- [ ] **Step 3: Add the early-skip branch to the single-section path**

In `src/local_deep_research/images/enhancer.py`, replace the existing single-section block at lines 338-342:

```python
        # Tiny reports (no headings): fall back to the single-shot path.
        # If per_section_candidates was provided, still partition (one
        # section, idx=0) so the prompt sees the section's filtered pool.
        if len(sections) == 1:
            if per_section_candidates is None:
                return self._run_enhance(markdown, candidates)
            section_candidates = per_section_candidates.get(0, [])
            return self._run_enhance(markdown, section_candidates)
```

with:

```python
        # Tiny reports (no headings): fall back to the single-shot path.
        # If per_section_candidates was provided, still partition (one
        # section, idx=0) so the prompt sees the section's filtered pool.
        if len(sections) == 1:
            if per_section_candidates is None:
                return self._run_enhance(markdown, candidates)
            section_candidates = per_section_candidates.get(0, [])
            if not section_candidates:
                logger.info(
                    "[IMG-TRACE] SECTION_SKIP idx=0 reason=empty_pool "
                    "heading='' candidates_in_section=0"
                )
                return markdown
            return self._run_enhance(markdown, section_candidates)
```

- [ ] **Step 4: Add the early-skip branch to the multi-section loop**

In `src/local_deep_research/images/enhancer.py`, replace the loop body at lines 344-361:

```python
        for idx, (heading, body) in enumerate(sections):
            chunk = (
                f"{heading}\n\n{body}".strip() if heading else body.strip()
            )
            if not chunk:
                continue
            if per_section_candidates is None:
                section_candidates = candidates  # legacy: full pool
            else:
                section_candidates = per_section_candidates.get(idx, [])
            enhanced_chunk = self._run_enhance(chunk, section_candidates)
            enhanced_parts.append(enhanced_chunk)
            logger.info(
                f"[IMG-TRACE] SECTION_ENHANCE idx={idx} "
                f"heading={heading[:80]!r} len_in={len(chunk)} "
                f"len_out={len(enhanced_chunk)} "
                f"candidates_in_section={len(section_candidates)}"
            )
        return "\n\n".join(enhanced_parts)
```

with:

```python
        for idx, (heading, body) in enumerate(sections):
            chunk = (
                f"{heading}\n\n{body}".strip() if heading else body.strip()
            )
            if not chunk:
                continue
            if per_section_candidates is None:
                section_candidates = candidates  # legacy: full pool
            else:
                section_candidates = per_section_candidates.get(idx, [])
            # Skip the LLM call when the per-section pool is empty —
            # the prompt would be a guaranteed no-op and ~2 s per
            # section is wasted. The section markdown passes through
            # unchanged; a SECTION_SKIP line is emitted so an operator
            # can see the skip in IMG-TRACE.
            if not section_candidates:
                logger.info(
                    f"[IMG-TRACE] SECTION_SKIP idx={idx} "
                    f"reason=empty_pool heading={heading[:80]!r} "
                    f"candidates_in_section=0"
                )
                enhanced_parts.append(chunk)
                continue
            enhanced_chunk = self._run_enhance(chunk, section_candidates)
            enhanced_parts.append(enhanced_chunk)
            logger.info(
                f"[IMG-TRACE] SECTION_ENHANCE idx={idx} "
                f"heading={heading[:80]!r} len_in={len(chunk)} "
                f"len_out={len(enhanced_chunk)} "
                f"candidates_in_section={len(section_candidates)}"
            )
        return "\n\n".join(enhanced_parts)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `pytest tests/images/test_per_section_domain_filter.py::test_enhance_single_section_empty_pool_skips_llm tests/images/test_per_section_domain_filter.py::test_enhance_multi_section_one_pool_empty_skips_that_section -v`
Expected: BOTH PASS.

- [ ] **Step 6: Run the full per-section test module to confirm no regression**

Run: `pytest tests/images/test_per_section_domain_filter.py -v`
Expected: ALL PASS — including `test_enhance_backward_compat_no_third_arg` (legacy full-pool path still calls the LLM per section), `test_enhance_per_section_partitioning_visible_to_llm` (populated pools still work), and `test_enhance_quick_summary_filters_to_cited_domains`.

- [ ] **Step 7: Commit (after confirming branch)**

Run:

```bash
git -C /home/administrator/local-deep-research rev-parse --abbrev-ref HEAD
```

Expected output: `main`

Run:

```bash
git -C /home/administrator/local-deep-research add src/local_deep_research/images/enhancer.py tests/images/test_per_section_domain_filter.py
git -C /home/administrator/local-deep-research commit -m "feat(images): skip LLM when per-section candidate pool is empty

Saves ~15 LLM calls (~30 s) per report by short-circuiting
ImageEnhancer.enhance() sections whose per_section_candidates[idx]
is []. The section markdown is appended unchanged; one
[IMG-TRACE] SECTION_SKIP line is emitted at INFO so the skip is
observable. The legacy full-pool path (per_section_candidates=None)
is unchanged. Replaces test_enhance_empty_per_section_pool_runs_but_no_images
(whose name encoded the wasteful behavior) with two tests that
assert the new contract."
```

Then: `git -C /home/administrator/local-deep-research log --oneline -3` and confirm the new commit is at HEAD.

---

### Task 2: Weight section-title token matches × 2 in `extract_segment_sources`

**Files:**
- Modify: `src/local_deep_research/images/relevance.py:373-412` (the `extract_segment_sources()` function — score formula only)
- Modify: `tests/images/test_segment_sources.py` (append four new tests; do not delete or modify existing tests)

**Interfaces:**
- Consumes: same inputs as today — `markdown: str`, `results: dict`, `top_n: int = 3`.
- Produces: same return shape — `list[tuple[str, str, list[str]]]`. The internal scoring changes from `len(section_terms & cand_terms)` to `2 * len(heading_terms & cand_terms) + 1 * len(body_terms & cand_terms)`. The `ratio` denominator is unchanged (`len(cand_terms)`). The `score >= 1 and ratio >= 0.30` threshold is unchanged.

- [ ] **Step 1: Add the failing tests**

Append the following four tests to the end of `tests/images/test_segment_sources.py`:

```python
# ---- Heading-weighted score (× 2 for matches in the section title) ----

def test_extract_segment_sources_heading_match_doubles_score():
    """A candidate whose only overlap with the section is one
    heading token must be allowed when its own title is short
    enough to keep the ratio above 0.30.

    Section heading is "广州塔"; the body has zero overlap with the
    candidate's title. The candidate title "广州塔珠江夜景" has
    6 own tokens under _match_terms (广州 / 塔 / 珠 / 江 / 夜 / 景),
    so the boosted score 2 / 6 ≈ 0.33 passes the 0.30 gate."""
    md = "## 广州塔\n\nshort body"
    results = {"findings": [
        {"search_results": [
            {"link": "https://ctrip.com/canton-tower",
             "title": "广州塔珠江夜景",
             "content": "",
             "snippet": ""},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    assert "https://ctrip.com/canton-tower" in out[0][2]


def test_extract_segment_sources_body_only_match_unchanged_behavior():
    """Regression guard: a candidate with one body-only match
    and a short own title still passes. Same as the pre-existing
    behavior — the heading weight does not regress body matches."""
    md = "## 景点\n\n推荐夜游珠江线路。"
    results = {"findings": [
        {"search_results": [
            {"link": "https://ctrip.com/yuejiang",
             "title": "夜游珠江",
             "content": "夜游珠江",
             "snippet": ""},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    assert "https://ctrip.com/yuejiang" in out[0][2]


def test_extract_segment_sources_diluted_body_match_still_dropped():
    """Regression guard for the existing 'drop long diluted candidate'
    rule: a candidate that matches a single body token in a long
    English snippet still has ratio below 0.30 and is dropped, even
    though it shares the section's topic broadly. Heading weight
    only helps when the heading itself overlaps — and here the
    heading is short while the candidate's own tokens are long, so
    any heading match on a 1-token heading dilutes to
    2 / len(cand_terms), still below 0.30 for long candidates."""
    # Section heading is a single short token; candidate has the
    # same short heading token plus 13 dilution tokens. heading_overlap
    # = 1, so heading×2 = 2; body_overlap = 0. score = 2.
    # len(cand_terms) = 14 (1 heading match + 13 dilution words).
    # ratio = 2/14 ≈ 0.14 < 0.30. Still dropped.
    md = "## Eiffel\n\nEiffel Tower info."
    results = {"findings": [
        {"search_results": [
            {"link": "https://a.com/eiffel",
             "title": "Eiffel Tower",
             "content": "Eiffel Tower Paris France landmark visit tours",
             "snippet": "tour Eiffel booking hotel travel guide"},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    assert "https://a.com/eiffel" not in out[0][2]


def test_extract_segment_sources_heading_match_does_not_save_tiny_candidate():
    """A candidate with 1 heading match but 100 own tokens still
    has ratio 2/100 ≈ 0.02 — heading weight is not a free pass.
    This is the worst-case false-positive guard."""
    # Section heading = "广州塔" → heading_terms = {广州, 塔}.
    # Candidate title = "广州塔" + 96 dilution English words.
    dilution = " ".join(f"word{i}" for i in range(96))
    md = "## 广州塔\n\nshort body"
    results = {"findings": [
        {"search_results": [
            {"link": "https://ctrip.com/ct",
             "title": f"广州塔 {dilution}",
             "content": "",
             "snippet": ""},
        ]}
    ]}
    out = extract_segment_sources(md, results)
    assert "https://ctrip.com/ct" not in out[0][2]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/images/test_segment_sources.py -v -k "heading_match or body_only_match or diluted_body_match or heading_match_does_not_save_tiny"`
Expected: ALL FOUR FAIL on the existing code — the new score formula is not in place.

- [ ] **Step 3: Update `extract_segment_sources` to use heading-weighted scoring**

In `src/local_deep_research/images/relevance.py`, replace the per-section scoring loop (lines 387-407):

```python
    for heading, body in sections:
        section_text = re.sub(r"^##\s+", "", heading) + "\n" + body
        section_terms = _match_terms(section_text)
        if not section_terms:
            out.append((heading, body, list(inherited)))
            continue
        scored: list[tuple[int, float, str]] = []
        for c in candidates:
            cand_terms = _match_terms(
                " ".join([c["title"], c["content"], c["snippet"]])
            )
            if not cand_terms:
                continue
            score = _score_match(section_terms, cand_terms)
            ratio = score / len(cand_terms)
            scored.append((score, ratio, c["url"]))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        allowed = [
            url for score, ratio, url in scored
            if score >= _MIN_SCORE and ratio >= _MIN_RATIO
        ][:top_n]
        if not allowed:
            allowed = list(inherited)
        out.append((heading, body, allowed))
        inherited = allowed
    return out
```

with:

```python
    for heading, body in sections:
        # Heading tokens carry tighter topical signal than body tokens
        # (the section heading is the most reliable statement of what
        # the section is about). Weight heading matches × 2 so a
        # candidate whose own title literally names the heading can
        # pass the score/ratio gate even when its body overlap is
        # weak. The ratio denominator is the candidate's own token
        # count, so heading weight does not save diluted candidates.
        heading_terms = _match_terms(
            re.sub(r"^##\s+", "", heading)
        )
        body_terms = _match_terms(body)
        section_terms = heading_terms | body_terms
        if not section_terms:
            out.append((heading, body, list(inherited)))
            continue
        scored: list[tuple[int, float, str]] = []
        for c in candidates:
            cand_terms = _match_terms(
                " ".join([c["title"], c["content"], c["snippet"]])
            )
            if not cand_terms:
                continue
            heading_overlap = heading_terms & cand_terms
            body_overlap = body_terms & cand_terms
            score = 2 * len(heading_overlap) + len(body_overlap)
            ratio = score / len(cand_terms)
            scored.append((score, ratio, c["url"]))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        allowed = [
            url for score, ratio, url in scored
            if score >= _MIN_SCORE and ratio >= _MIN_RATIO
        ][:top_n]
        if not allowed:
            allowed = list(inherited)
        out.append((heading, body, allowed))
        inherited = allowed
    return out
```

- [ ] **Step 4: Run the four new tests to verify they pass**

Run: `pytest tests/images/test_segment_sources.py -v -k "heading_match or body_only_match or diluted_body_match or heading_match_does_not_save_tiny"`
Expected: ALL FOUR PASS.

- [ ] **Step 5: Run the full segment-sources test module to confirm no regression**

Run: `pytest tests/images/test_segment_sources.py -v`
Expected: ALL PASS — including the pre-existing strict-threshold tests `test_extract_segment_sources_drops_weak_single_token_match`, `test_extract_segment_sources_drops_long_diluted_candidate`, and `test_extract_segment_sources_drops_passing_mention` (lines 69-141).

- [ ] **Step 6: Commit (after confirming branch)**

Run:

```bash
git -C /home/administrator/local-deep-research rev-parse --abbrev-ref HEAD
```

Expected output: `main`

Run:

```bash
git -C /home/administrator/local-deep-research add src/local_deep_research/images/relevance.py tests/images/test_segment_sources.py
git -C /home/administrator/local-deep-research commit -m "feat(images): weight section-title token matches × 2 in extract_segment_sources

Heading tokens are tighter topical signal than body tokens. A
candidate whose title literally names the section heading (e.g.
'广州塔' in both) now scores 2 per heading match instead of 1, so
genuine on-topic candidates survive the existing
score >= 1 AND ratio >= 0.30 gate when the body overlap is weak.
The ratio denominator stays len(cand_terms), so heading weight
cannot save diluted candidates (a 1-heading-match / 100-token
candidate still has ratio 0.02 and is dropped). Threshold,
inherited-allow-list fallback, and public signature are unchanged.

Adds four tests covering: heading-only match allowed, body-only
match unchanged, diluted body match still dropped, and 100-token
diluted candidate with 1 heading match still dropped."
```

Then: `git -C /home/administrator/local-deep-research log --oneline -3` and confirm both new commits are at HEAD on `main`.

---

### Task 3: Full image test suite + e2e regression

**Files:** no source changes — verification only.

- [ ] **Step 1: Run the full `tests/images/` suite**

Run: `pytest tests/images/ -v`
Expected: ALL PASS. This includes:
- `test_enhancer.py` — legacy LLM behavior contracts.
- `test_per_section_domain_filter.py` — eTLD+1 filter and the new empty-pool skip.
- `test_segment_sources.py` — heading-weighted scoring and pre-existing strict-threshold rules.
- `test_relevance.py` — entity-extraction gate (untouched by this change; regression guard).
- `test_postprocessing.py` and `test_postprocessing_e2e.py` — the full-pipeline e2e cross-domain filter test added in commit `437d9909`.
- `test_bank.py`, `test_extractor.py`, `test_store.py`, `test_vision.py`, `test_models.py` — bank/store/vision contracts.

- [ ] **Step 2: Run the related modules' tests**

Run: `pytest tests/ -v -k "image or vision or relevance or postprocessing or per_section or segment_sources"`
Expected: ALL PASS. Catches anything in the wider project that touches the changed functions (e.g. snapshot, fetch, scrape, or store tests that re-use `_match_terms` or `extract_segment_sources`).

- [ ] **Step 3: Confirm the SUMMARY drop-count path still aggregates**

In `git log --oneline -10` confirm the most recent two commits are the ones from Tasks 1 and 2. Then re-read `src/local_deep_research/images/postprocessing.py` and confirm the SUMMARY line format is unchanged (we did not touch it). No new drop counter was added in this plan.

- [ ] **Step 4: Final verification — re-run the two new skip tests + four new heading tests**

Run:

```bash
pytest tests/images/test_per_section_domain_filter.py::test_enhance_single_section_empty_pool_skips_llm \
       tests/images/test_per_section_domain_filter.py::test_enhance_multi_section_one_pool_empty_skips_that_section \
       tests/images/test_segment_sources.py::test_extract_segment_sources_heading_match_doubles_score \
       tests/images/test_segment_sources.py::test_extract_segment_sources_body_only_match_unchanged_behavior \
       tests/images/test_segment_sources.py::test_extract_segment_sources_diluted_body_match_still_dropped \
       tests/images/test_segment_sources.py::test_extract_segment_sources_heading_match_does_not_save_tiny_candidate \
       -v
```

Expected: 6 passed. Confirms both feature changes still work end-to-end after the full suite has run.
