# Image Enhancer: Early Skip + Section-Title Token Weighting

**Date:** 2026-07-29
**Status:** Proposed for review
**Scope:** `src/local_deep_research/images/enhancer.py` and `src/local_deep_research/images/relevance.py` (per-section source-URL matching) only

## 1. Problem

Two independent inefficiencies in the report-image pipeline, both surfaced by reading the latest commits and the post-2026-07-25 production telemetry:

1. **Wasted LLM calls when a section has zero candidates.** `ImageEnhancer.enhance()` (enhancer.py:310-362) splits the report into sections and calls `_run_enhance()` for every section, even when `per_section_candidates[idx]` is an empty list after the eTLD+1 same-source filter. With ~15 sections per report and ~2 s per LLM call, this costs ~30 s per report on a no-op path. The existing test `test_enhance_empty_per_section_pool_runs_but_no_images` (test_per_section_domain_filter.py:227) *encodes* the wasteful behavior, so it will need to be updated.

2. **Section title token matches carry no extra weight.** `extract_segment_sources()` (relevance.py:373-412) builds `section_text = heading + "\n" + body` and scores each candidate as `score = |section_terms ∩ cand_terms|`. A candidate whose only overlap is one heading token (e.g. section titled "广州塔" with a tiktok.com image whose `title` contains only "广州塔珠江夜景") currently scores the same as a candidate whose overlap is in a body word like "城市". The same-source eTLD+1 gate downstream then compares against whatever the section *cited* — which for a section heading like "广州塔" is *also* heading-aligned — and drops the candidate even though the candidate's own title literally names the section. The right model is: heading tokens are tighter signal than body tokens; they should be weighted higher so genuine on-topic candidates survive while genuinely off-topic candidates (where the only match is a coincidental body word) still get dropped.

Both are behavior changes scoped to the per-section image path, with no changes to the bank, the Vision describer, the DB schema, the URL rewriter, or the image-download pipeline.

## 2. Goals

- Skip the LLM call entirely when the per-section candidate pool is empty; the section markdown passes through unchanged.
- When the candidate pool is empty, emit one IMG-TRACE line so the SUMMARY drop counter is accurate and an operator can see why the LLM wasn't called.
- Weight heading-token matches × 2 in the per-section overlap score; body-token matches keep their current × 1 weight.
- Keep the existing `score >= 1` AND `ratio >= 0.30` thresholds unchanged. (Ratio continues to be computed against the candidate's own token count, not the section's, so heading-weighted scores still face the same bar — title weight cannot manufacture a pass on a candidate that has only one weak body match.)
- Add unit tests for both new behaviors; update the one existing test that codified the wasteful behavior.

## 3. Non-goals

- No change to `_PROMPT`, to the LLM client, or to retry behavior.
- No change to the per-section domain filter, the bank, the Vision describer, the image-store download path, or DB schema.
- No change to the entity-extraction gate in `evaluate_candidate()` (relevance.py:781-955). The title-weighting change is scoped to `extract_segment_sources()` only, which is the *upstream* matching step that builds the per-section allowed URL set feeding the eTLD+1 filter.
- No new "title-only" pass condition. A candidate still needs a real body-side match in the absence of a heading match; the title weight is a *boost* on top of the existing rule, not a replacement for it.
- No change to the inherited-allow-list fallback (lines 408-409) — an orphan section between two matched sections continues to inherit.

## 4. Design — Change 1: Early Skip on Empty Per-Section Pool

### 4.1 Where

`ImageEnhancer.enhance()` (enhancer.py:310-362). Two call sites today issue `_run_enhance()` against a candidate list that may be empty:

- **Single-section path (lines 338-342):** `if len(sections) == 1: ... self._run_enhance(markdown, section_candidates)`. If `section_candidates == []`, skip the LLM and return the markdown unchanged.
- **Multi-section path (lines 343-355):** the loop calls `self._run_enhance(chunk, section_candidates)` unconditionally. If `section_candidates == []`, skip the LLM and append `chunk` unchanged.

### 4.2 New behavior

When `section_candidates` is empty:

- Do **not** call `_run_enhance()`.
- Append the section chunk unchanged to `enhanced_parts` (or, in the single-section path, return `markdown`).
- Emit one IMG-TRACE line: `[IMG-TRACE] SECTION_SKIP idx={idx} reason=empty_pool heading={heading[:80]!r} candidates_in_section=0`
- The existing `SECTION_ENHANCE` log line is **not** emitted for skipped sections (it's a "we did the work" marker; a skip is the absence of work).

The "early skip" never applies to the legacy full-pool path (`per_section_candidates is None`); that path passes the entire bank to the LLM and is covered by `test_enhance_backward_compat_no_third_arg`. Skipping there would change the legacy contract and is out of scope.

### 4.3 Test updates

- **Replace** `test_enhance_empty_per_section_pool_runs_but_no_images` (test_per_section_domain_filter.py:227) with a new test that asserts: (a) `llm.calls == []` (zero LLM calls), (b) the section is passed through unchanged, (c) the SUMMARY drop counter (`dropped_no_candidates`) increments by 1 if/when a drop counter is wired into `enhance()`'s return value — see §4.4.
- **Add** a multi-section variant: per-section pools `{0: [img_a], 1: []}` with two sections in the markdown; assert `len(llm.calls) == 1`, and the second section's body is present unchanged in the output.

### 4.4 Drop counter

The existing SUMMARY line aggregates per-section `_candidates_for_section` drop counts (commit 68657caa). We will **not** wire a new `dropped_no_candidates` counter in this design; the skip is observable via the IMG-TRACE `SECTION_SKIP` line, and touching the SUMMARY builder is out of scope.

## 5. Design — Change 2: Section-Title Token Weighting

### 5.1 Where

`extract_segment_sources()` (relevance.py:373-412), specifically the per-candidate scoring loop (lines 393-407).

### 5.2 Current scoring

```python
section_text = re.sub(r"^##\s+", "", heading) + "\n" + body
section_terms = _match_terms(section_text)
...
cand_terms = _match_terms(" ".join([c["title"], c["content"], c["snippet"]]))
score = _score_match(section_terms, cand_terms)
ratio = score / len(cand_terms)
```

`section_terms` is the union of heading and body tokens. `score` is the raw overlap count. `ratio` is `score / len(cand_terms)` (the candidate's own token count, NOT the section's).

### 5.3 New scoring

```python
heading_terms = _match_terms(heading)
body_terms = _match_terms(body)
section_terms = heading_terms | body_terms
...
cand_terms = _match_terms(" ".join([c["title"], c["content"], c["snippet"]]))
if not cand_terms:
    continue
heading_overlap = heading_terms & cand_terms
body_overlap = body_terms & cand_terms
score = 2 * len(heading_overlap) + 1 * len(body_overlap)
ratio = score / len(cand_terms)
```

Threshold check stays as: `if score >= 1 and ratio >= 0.30`. With this formula:

- A candidate whose only overlap is one heading token (e.g. `广州塔` in both the section heading and the candidate title) and whose `len(cand_terms) == 6` scores `2 / 6 ≈ 0.33` → passes.
- A candidate with 1 body overlap and 0 heading overlap, with `len(cand_terms) == 6` scores `1 / 6 ≈ 0.17` → fails (same as today).
- A candidate with 2 body overlaps and 0 heading, with `len(cand_terms) == 5` scores `2 / 5 = 0.40` → passes (same as today).
- A candidate with 1 heading overlap and 1 body overlap scores `2 + 1 = 3` → robustly passes any reasonable ratio.

This matches the user's selected design: weight × 2, no normalization change, no threshold change.

### 5.4 Heading-text normalization

`section_text` is currently built as `re.sub(r"^##\s+", "", heading) + "\n" + body`. The strip-`##` happens **only** on the section_text used for `section_terms`; the heading line in the final report keeps its `##` markup. We will reuse the same `re.sub` for `heading_terms` so a heading like "## 越秀公园" tokenizes as `{"越秀", "公园"}` (a 4-char CJK run splits into 2-char and 2-char spans via `_match_terms`).

### 5.5 Sort and top-N

`scored.sort(key=lambda x: (x[0], x[1]), reverse=True)` — unchanged. The sort already prefers higher score, then higher ratio; the new formula preserves that.

`allowed[:top_n]` — unchanged.

### 5.6 Re-export

`from local_deep_research.images.postprocessing import extract_segment_sources` (used by test_segment_sources.py:12) must continue to work. The function stays in `relevance.py` and `postprocessing.py` continues to re-export it. No change to the re-export.

## 6. Data Flow Summary

```
                   ┌─────────────────────────────────────────┐
                   │  research_results (findings w/ URLs)   │
                   └─────────────────────────────────────────┘
                                     │
                                     ▼
                   ┌─────────────────────────────────────────┐
                   │  extract_segment_sources()             │
                   │  - heading_terms, body_terms           │  ← Change 2
                   │  - score = 2|h∩c| + 1|b∩c|             │  ← Change 2
                   │  - ratio = score / len(cand_terms)     │
                   │  - threshold: score>=1, ratio>=0.30    │
                   │  - inherit previous allow-list if no   │
                   │    candidate passes                     │
                   └─────────────────────────────────────────┘
                                     │  per-section allowed URLs
                                     ▼
                   ┌─────────────────────────────────────────┐
                   │  build_section_allowed_domains()       │
                   │  eTLD+1 of cited URLs per section       │
                   └─────────────────────────────────────────┘
                                     │  per-section allowed domains
                                     ▼
                   ┌─────────────────────────────────────────┐
                   │  _candidates_for_section()              │
                   │  keeps only candidates whose           │
                   │  source_url eTLD+1 ∈ allowed_domains   │
                   └─────────────────────────────────────────┘
                                     │  per-section candidate list
                                     ▼
                   ┌─────────────────────────────────────────┐
                   │  ImageEnhancer.enhance()                │
                   │  - split into sections                  │
                   │  - per-section pool == [] ?             │  ← Change 1
                   │      → skip LLM, pass section through   │  ← Change 1
                   │      → log SECTION_SKIP IMG-TRACE       │  ← Change 1
                   │  - else → call _run_enhance()           │
                   └─────────────────────────────────────────┘
                                     │
                                     ▼
                              enhanced markdown
```

## 7. Files Touched

- `src/local_deep_research/images/enhancer.py` — early skip in `enhance()` (single + multi-section paths); one IMG-TRACE log line.
- `src/local_deep_research/images/relevance.py` — split `section_terms` into `heading_terms | body_terms`; change score formula inside `extract_segment_sources()`.
- `tests/images/test_per_section_domain_filter.py` — replace the "runs but no images" test with a "skips LLM" test; add a multi-section empty-pool variant.
- `tests/images/test_segment_sources.py` — add tests for heading-weighted scoring.

## 8. Testing Strategy

### 8.1 Unit tests (test_per_section_domain_filter.py)

- `test_enhance_single_section_empty_pool_skips_llm`: 1 section, `per_section_candidates={0: []}`, assert `llm.calls == []`, output == input.
- `test_enhance_multi_section_one_pool_empty_skips_that_section`: 2 sections, `per_section_candidates={0: [img_a], 1: []}`, assert `len(llm.calls) == 1`, output contains both section bodies.
- `test_enhance_full_pool_path_still_calls_llm_when_per_section_none`: unchanged from `test_enhance_backward_compat_no_third_arg`.
- `test_enhance_quick_summary_filters_to_cited_domains`: unchanged.
- The legacy test `test_enhance_empty_per_section_pool_runs_but_no_images` is **replaced** (not kept) by the first test above.

### 8.2 Unit tests (test_segment_sources.py)

- `test_extract_segment_sources_heading_match_doubles_score`: section heading is "广州塔", body is short, candidate title is "广州塔珠江夜景" with extra dilution tokens. Under the new formula, the candidate scores `2` (one heading match, no body match); the ratio gate against `len(cand_terms)` passes. Confirm the candidate URL is on the section's allow-list.
- `test_extract_segment_sources_body_only_match_unchanged_behavior`: section heading is generic ("景点"), body contains "夜游珠江" once; candidate title is "夜游珠江". Score = 1 (body match), ratio is high enough → passes (regression guard).
- `test_extract_segment_sources_diluted_body_match_still_dropped`: regression — same case as `test_extract_segment_sources_drops_long_diluted_candidate` (line 96) still drops.
- `test_extract_segment_sources_heading_match_does_not_save_tiny_candidate`: candidate has 1 heading match and 100 own tokens → score=2, ratio=0.02 → still dropped. (Heading weight is not a free pass.)

### 8.3 Existing tests that must still pass

- `test_per_section_domain_filter.py` — all domain/registry tests.
- `test_enhancer.py` — legacy LLM-behavior tests.
- `test_postprocessing*.py` — full-pipeline tests, including the e2e cross-domain filter test added in commit 437d9909.
- `test_relevance.py` — entity-extraction tests (these are upstream of the change; they don't go through `extract_segment_sources`).

### 8.4 Manual smoke

Run the existing e2e cross-domain test (`test_postprocessing_e2e`) once to confirm SUMMARY drop counts still aggregate correctly.

## 9. Rollout

- Single feature branch off `main`, two commits: (1) early-skip, (2) heading-weighting. Reuse the commit-style of the recent images feature work (`feat(images): ...`).
- Follow the commit-workflow rules: `main` is the only active branch; `git rev-parse --abbrev-ref HEAD` before every commit; confirm `git log --oneline -3` after every commit.
- No new config flags. The changes are unconditional behavior improvements.

## 10. Risk

- **False-positive allow-listing via heading weight.** A candidate that hits 1 heading token but is otherwise unrelated could pass the threshold. Mitigation: the eTLD+1 same-source filter downstream still requires the candidate's `source_url` to be on the section's allow-list of *cited* URLs, which is built from the same `extract_segment_sources` output. A candidate with only 1 heading-token overlap and a long candidate body still has a low `ratio` and is dropped. Unit test `test_extract_segment_sources_heading_match_does_not_save_tiny_candidate` covers the worst case.
- **Test rewrite for `test_enhance_empty_per_section_pool_runs_but_no_images`.** This is a deliberate behavior change; the test was encoding waste, not correctness. The commit message must explain the inversion.
- **Thresholds unchanged.** Recent direction (commit 5907c1ec) is to tighten. The heading-weight boost is a one-sided improvement for high-confidence candidates; it does not loosen the existing `score >= 1` AND `ratio >= 0.30` rule.

## 11. Open Questions

- **None blocking.** The user pre-selected both design choices: scope (both changes) and weighting formula (weight × 2, no normalization, no threshold change). The implementation in §5.3 reflects that selection.
