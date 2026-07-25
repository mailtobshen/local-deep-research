# Strict Context-Entity Image Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, text-only named-entity relevance gate for report images, use one filtered Eligible Bank throughout the pipeline, and stop section fallback and Vision-based candidate admission.

**Architecture:** Build a small `images/relevance.py` module that extracts named entities from original alt text, builds a report-context entity model from the current query/report/search results, classifies source strength, and returns reason-coded keep/drop decisions. `postprocessing.py` constructs a raw bank, gates it once, passes only an Eligible Bank to the LLM enhancer, skips section fallback, and persists only LLM-selected URLs. `ImageBank.subset()` provides the public boundary needed to avoid private-map access.

**Tech Stack:** Python 3.12+, dataclasses, pytest, existing BeautifulSoup/extraction models, Loguru, current LangChain LLM interface (not used for visual review).

## Global Constraints

- The gate is fail-closed: missing alt, no named entity, extraction failure, unresolved entity relationship, entity conflict, or context-build failure rejects the candidate.
- Only the original extracted alt can satisfy the first gate; do not generate alt text for this pipeline.
- A substantial-anchor threshold applies: CJK entities need >= 3 characters, Latin entities >= 5 characters; shorter spans are rejected as `no_named_entity`.
- A candidate's `source_url` must be cited in some section's `SECTION_SOURCES` pool; an uncited `source_url` is a hard drop (`source_url_not_cited`). There is no `context_entity_rescue` path.
- Only current-run query, report, section, search-result, citation, and source-title context may establish entity relationships; no external knowledge-base lookup.
- No visual or multimodal relevance verification; do not call `VisionDescriber.describe()` for admission.
- No section fallback or post-LLM image backfill; image-free sections remain image-free.
- LLM enhancement and all later selection must consume only the filtered Eligible Bank.
- Keep existing download, persistence, resizing, database, and Markdown URL rewrite behavior unchanged.
- Do not relax rules because too few images remain.
- Preserve reason-coded `[IMG-TRACE]` observability.

---

## File Map

- Create: `src/local_deep_research/images/relevance.py` — report-context model, named-entity extraction, source signal classification, fail-closed keep/drop decisions.
- Create: `tests/images/test_relevance.py` — unit tests for entity extraction, context relations, conflict handling, source-URL citation, and fail-closed behavior.
- Modify: `src/local_deep_research/images/bank.py` — add public `subset(urls)` method.
- Modify: `src/local_deep_research/images/postprocessing.py` — replace source-only gate with entity gate, build one Eligible Bank, remove fallback call, emit gate summaries.
- Modify: `src/local_deep_research/images/enhancer.py` — ensure this report path never invokes `_vision_fill()`; retain textual LLM prompt constraints.
- Modify: `tests/images/test_postprocessing.py` — update fixtures for named entities and test Eligible Bank isolation, disabled fallback, and no Vision invocation.
- Modify: `tests/images/test_enhancer.py` (if present; otherwise add focused tests beside existing enhancer tests) — verify no report candidate admission through Vision.

---

### Task 1: Add the public ImageBank subset boundary

**Files:**
- Modify: `src/local_deep_research/images/bank.py:11-55`
- Test: `tests/images/test_bank.py` if present; otherwise `tests/images/test_relevance.py`

**Interfaces:**
- Consumes: an iterable of URL strings and the existing `_by_url` records.
- Produces: `ImageBank.subset(urls: Iterable[str]) -> ImageBank`, preserving input order and silently ignoring unknown URLs.

- [ ] **Step 1: Write the failing test**

```python
from local_deep_research.images.bank import ImageBank
from local_deep_research.images.extractor import ExtractedImage


def image(url):
    return ExtractedImage(url, "广州塔", "https://source", "", None, None)


def test_subset_preserves_order_and_does_not_expose_private_map():
    bank = ImageBank()
    bank.add([image("https://a"), image("https://b")])

    subset = bank.subset(["https://b", "https://missing", "https://a"])

    assert subset.all_urls() == ["https://b", "https://a"]
    assert subset.candidates_with_alt()[0].url == "https://b"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_bank.py -q
```

Expected: FAIL because `ImageBank` has no `subset` method. If the repository has no `test_bank.py`, run the test after adding it to `tests/images/test_relevance.py` and expect the same `AttributeError`.

- [ ] **Step 3: Implement the minimal public subset method**

Add `Iterable` to the typing imports and implement:

```python
    def subset(self, urls: Iterable[str]) -> "ImageBank":
        selected = ImageBank()
        selected.add(
            [self._by_url[url] for url in urls if url in self._by_url]
        )
        return selected
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_bank.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the isolated boundary change**

```bash
git add src/local_deep_research/images/bank.py tests/images/test_bank.py
 git commit -m "refactor(images): add public image bank subsets"
```

---

### Task 2: Implement named-entity extraction and report context

**Files:**
- Create: `src/local_deep_research/images/relevance.py`
- Create: `tests/images/test_relevance.py`

**Interfaces:**
- Produces `ReportEntityContext`, `ImageRelevanceDecision`, `build_report_entity_context()`, and `evaluate_candidate()`.
- `build_report_entity_context(clean_markdown: str, results: dict, query: str = "") -> ReportEntityContext` must never use external knowledge.
- `evaluate_candidate(candidate: ExtractedImage, context: ReportEntityContext) -> ImageRelevanceDecision` is pure and text-only.

- [ ] **Step 1: Write failing unit tests for the hard gate and context matches**

```python
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.relevance import (
    build_report_entity_context,
    evaluate_candidate,
)


def candidate(alt, source="https://source.example/page"):
    return ExtractedImage("https://img.example/a.jpg", alt, source, "", None, None)


def test_generic_alt_is_rejected_before_source_matching():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 广州塔\n广州塔是地标。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("旅游景点攻略"), context)
    assert decision.status == "drop"
    assert decision.reason == "no_named_entity"


def test_named_entity_confirmed_by_section_is_kept():
    context = build_report_entity_context(
        "# 广州近代建筑\n## 中山纪念堂\n中山纪念堂位于广州。",
        {"findings": []},
        query="广州近代建筑",
    )
    decision = evaluate_candidate(candidate("中山纪念堂"), context)
    assert decision.status == "keep"
    assert 0 in decision.matched_sections


def test_foreign_entity_is_rejected_even_when_source_looks_relevant():
    context = build_report_entity_context(
        "# 广州旅游\n## 广州景点\n广州塔。",
        {"findings": [{"search_results": [{
            "url": "https://instagram.example/popular/广州景点",
            "title": "广州景点",
        }]}]},
        query="广州旅游",
    )
    decision = evaluate_candidate(
        candidate("第一次来重庆，别只玩市区景点", "https://instagram.example/popular/广州景点"),
        context,
    )
    assert decision.status == "drop"
    assert decision.reason == "foreign_entity_conflict"
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_relevance.py -q
```

Expected: FAIL because `images/relevance.py` and its interfaces do not yet exist.

- [ ] **Step 3: Implement the context and decision dataclasses**

Define:

```python
@dataclass(frozen=True)
class ReportEntityContext:
    primary_entities: frozenset[str]
    section_entities: tuple[frozenset[str], ...]
    all_entities: frozenset[str]
    entity_relations: frozenset[tuple[str, str, str]]
    section_sources: tuple[tuple[str, str, tuple[str, ...]], ...]
    all_cited_source_urls: frozenset[str]
```

`all_cited_source_urls` is the union of every section's `SECTION_SOURCES` URLs and is the set used by the hard Step 5 same-origin check.

@dataclass(frozen=True)
class ImageRelevanceDecision:
    url: str
    status: Literal["keep", "drop"]
    reason: str
    entities: frozenset[str]
    matched_sections: frozenset[int]
    source_signal: Literal["strong", "weak", "none"]
    evidence_refs: tuple[str, ...]
```

Use normalized strings for matching but preserve the original candidate and evidence references in the decision/log payload.

- [ ] **Step 4: Implement deterministic named-entity extraction**

Use a small, explicit extractor suitable for the current-run context:

1. Collect proper-name candidates from the query, headings, source titles, and text using CJK/Latin contiguous spans and known title-like phrases.
2. Exclude the documented generic vocabulary (`旅游`, `景点`, `攻略`, `建筑`, `夜景`, `美食`, `交通`, `推荐`, `图片`, and their English equivalents).
3. Treat a candidate alt entity as named only when it is either:
   - a multi-character proper-name span present in the current report context; or
   - a location/organization/attraction span identified by explicit current-context evidence.
4. Never treat a generic topic word alone as a named entity.
5. Raise/return a controlled extraction failure result instead of allowing the candidate through.

Keep the extractor pure and dependency-light; do not add an external NER package.

- [ ] **Step 5: Implement `build_report_entity_context()`**

Collect current-run text from:

```python
query
clean_markdown title and ## sections
finding.search_results[].title
finding.search_results[].content or snippet
finding.search_results[].url or link
finding.search_results[].source_title
```

Use the existing `extract_segment_sources()` output for section source candidates, but store it as auxiliary evidence. Derive section and report entities from the combined text. Record only explicit entity relations found in current-run text, including simple location phrases such as `X位于Y`, `X在Y`, and `Y的X` when both X and Y are known entities.

If `results` is malformed or context cannot be safely built, return a context object marked unusable or raise a dedicated internal error that `postprocessing.py` converts to `context_build_failed` and an empty Eligible Bank.

- [ ] **Step 6: Implement `evaluate_candidate()` fail-closed decisions**

Apply this order:

```python
if not candidate.alt.strip():
    drop("missing_alt")
entities = extract_named_entities(candidate.alt, context)
if extraction_failed:
    drop("entity_extraction_failed")
if not entities:
    drop("no_named_entity")
# Substantial-anchor thresholds: CJK >= 3 chars, Latin >= 5 chars.
if any_entity_below_substantial_threshold(entities):
    drop("no_named_entity")
if explicit_foreign_or_entity_conflict(entities, context):
    drop("foreign_entity_conflict")
matched_sections = sections_with_explicit_entity_evidence(entities, context)
if not matched_sections and not explicit_report_relation(entities, context):
    drop("unresolved_entity_relation")
# Hard Step 5: source_url must be cited in some section's SECTION_SOURCES pool.
if candidate.source_url not in context.all_cited_source_urls:
    drop("source_url_not_cited")
return keep("context_match")
```

Source signal must never override a conflict. Aggregation/feed URLs are weak evidence. The `context_entity_rescue` path is removed: an uncited `source_url` is a hard drop.

- [ ] **Step 7: Add uncited-source-URL and unresolved tests**

```python
def test_uncited_source_url_drops_context_confirmed_entity():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔位于广州。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(
        candidate("广州塔珠江夜景", "https://unmapped.example/photo"), context
    )
    assert decision.status == "drop"
    assert decision.reason == "source_url_not_cited"


def test_named_entity_without_current_context_is_rejected():
    context = build_report_entity_context(
        "# 广州建筑\n## 广州塔\n广州塔。",
        {"findings": []},
        query="广州建筑",
    )
    decision = evaluate_candidate(candidate("某地中山纪念堂"), context)
    assert decision.status == "drop"
    assert decision.reason == "unresolved_entity_relation"
```

- [ ] **Step 8: Run the unit tests**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_relevance.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit the gate module**

```bash
git add src/local_deep_research/images/relevance.py tests/images/test_relevance.py
 git commit -m "feat(images): add strict context entity relevance gate"
```

---

### Task 3: Integrate the gate and remove section fallback from production

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py:308-477`
- Modify: `src/local_deep_research/images/enhancer.py:116-129`
- Test: `tests/images/test_postprocessing.py`

**Interfaces:**
- Consumes: `ImageBank.subset()`, `build_report_entity_context()`, and `evaluate_candidate()` from Task 2.
- Produces: one `eligible_bank` used by enhancer and the existing persistence path; no production call to `fill_section_images()`.

- [ ] **Step 1: Write failing integration tests**

```python
from unittest.mock import MagicMock, patch
from local_deep_research.images import postprocessing


def test_postprocessing_passes_only_entity_eligible_images_to_enhancer():
    findings = [{"search_results": [{
        "url": "https://source/page",
        "title": "广州塔",
        "html_content": '[{"url":"https://img/guangzhou.jpg","alt":"广州塔","source_url":"https://source/page"},'
                       '{"url":"https://img/chongqing.jpg","alt":"重庆洪崖洞","source_url":"https://source/page"}]',
    }]}]
    with patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_cls, \
         patch.object(postprocessing, "ImageStore") as store_cls, \
         patch.object(postprocessing, "fill_section_images", side_effect=AssertionError("fallback called")):
        enhancer_cls.return_value.enhance.return_value = "# 广州建筑\n## 广州塔\n![广州塔](https://img/guangzhou.jpg)"
        store_cls.return_value.persist.return_value = {"https://img/guangzhou.jpg": "/images/rid/a.jpg"}
        postprocessing.enhance_report_with_images(
            research_id="rid",
            clean_markdown="# 广州建筑\n## 广州塔\n广州塔介绍",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
        passed_bank = enhancer_cls.return_value.enhance.call_args.args[1]
        assert passed_bank.all_urls() == ["https://img/guangzhou.jpg"]


def test_no_eligible_bank_skips_enhancer_and_preserves_markdown():
    with patch.object(postprocessing, "ImageEnhancer") as enhancer_cls:
        result = postprocessing.enhance_report_with_images(
            research_id="rid",
            clean_markdown="# 广州建筑\n## 广州塔\n介绍",
            results={"findings": [{"search_results": [{
                "html_content": '[{"url":"https://img/a.jpg","alt":"旅游攻略"}]'
            }]}]},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    assert result == "# 广州建筑\n## 广州塔\n介绍"
    enhancer_cls.assert_not_called()
```

- [ ] **Step 2: Run the integration tests and verify they fail**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_postprocessing.py -q
```

Expected: FAIL because the current code passes raw candidates to fallback and does not apply the new entity gate.

- [ ] **Step 3: Replace source-only filtering with one entity-gated Eligible Bank**

In `enhance_report_with_images()`:

1. Keep raw Bank construction and raw candidate logging.
2. Build `ReportEntityContext` from `clean_markdown`, `results`, and the available research query if that value is already passed by the caller; do not invent a new external input if the current signature lacks it.
3. Evaluate every raw candidate and count reason-coded decisions.
4. Construct `eligible_bank = bank.subset(kept_urls)`.
5. Do not build a separate `bank_for_enhancer` from only source prefixes.
6. Pass `eligible_bank` to `ImageEnhancer`.
7. For metadata lookup before persistence, continue using the original raw bank because it contains the metadata for URLs already selected by the enhancer; assert/log if a selected URL is not present in the eligible bank and exclude it from persistence.

The conceptual replacement is:

```python
context = build_report_entity_context(clean_markdown, results, query=query)
decisions = [evaluate_candidate(image, context) for image in bank.candidates_with_alt()]
kept_urls = [d.url for d in decisions if d.status == "keep"]
eligible_bank = bank.subset(kept_urls)
log_entity_gate_summary(research_id, decisions)
logger.info(
    f"[IMG-TRACE] ELIGIBLE_BANK research={research_id} total={len(eligible_bank.all_urls())}"
)
if not eligible_bank.all_urls():
    logger.info(f"[IMG-TRACE] SECTION_FALLBACK research={research_id} status=disabled")
    logger.info(f"[IMG-TRACE] END research={research_id} status=empty")
    return clean_markdown

enhanced = enhancer.enhance(clean_markdown, eligible_bank)
enhanced, original_count, unique_count = _dedupe_images(enhanced)
chosen = [match.group(2) for match in _IMG_RE.finditer(enhanced)]
```

Preserve the existing persistence and rewrite logic after `chosen`; no fallback selection occurs.

- [ ] **Step 4: Enforce the hard source-URL same-origin check**

After building `ReportEntityContext`, every kept candidate must additionally pass a hard `source_url` citation check. The candidate's `source_url` must be present in `context.all_cited_source_urls` (the union of all sections' `SECTION_SOURCES` URLs, derived from current-run `extract_segment_sources()` output).

In `evaluate_candidate()` this is already enforced by Task 2 Step 6 (`drop("source_url_not_cited")`); this step verifies the integration point and the cited-source pool feeding it:

1. Confirm `build_report_entity_context()` populates `all_cited_source_urls` from the section sources already logged as `[IMG-TRACE] SECTION_SOURCES` per section (the existing production log added for this purpose).
2. Confirm there is no `context_entity_rescue` path anywhere in `relevance.py` or `postprocessing.py`; a grep must return zero matches:

```bash
grep -R "context_entity_rescue" -n src/local_deep_research/images
```

Expected: no matches. Any prior `rescue` branch is replaced by the hard `source_url_not_cited` drop.

3. The conceptual check inside the gate is:

```python
if candidate.source_url not in context.all_cited_source_urls:
    drop("source_url_not_cited")
```

This trades coverage for precision: a candidate whose alt matches context but whose `source_url` was not cited by any section is dropped. No external lookup is performed.

- [ ] **Step 5: Remove the production fallback call**

Delete the production block equivalent to:

```python
candidates = bank.candidates_with_alt()
before_fallback = len([...])
enhanced = fill_section_images(enhanced, candidates)
```

Replace its summary with:

```python
logger.info(
    f"[IMG-TRACE] SECTION_FALLBACK research={research_id} status=disabled"
)
```

The standalone `fill_section_images()` function may remain unused in this change.

- [ ] **Step 6: Disable Vision candidate admission in the report path**

Change the enhancer integration so report postprocessing constructs or uses an enhancer mode that never calls `_vision_fill()`. The minimal implementation is to add an explicit constructor parameter:

```python
allow_vision_fill: bool = False
```

and guard the existing branch:

```python
if self.allow_vision_fill and len(candidates) <= self.min_alt_count and self.vision.enabled:
    self._vision_fill(bank)
```

Instantiate it from postprocessing with `allow_vision_fill=False`. Do not delete `VisionDescriber` or its unrelated public behavior.

- [ ] **Step 7: Update postprocessing tests for named entities, no fallback, and no Vision**

Change existing generic fixture alts such as `tower` to context-confirmed named alts such as `广州塔`. Replace the old tests that expect Vision to receive missing-alt configuration with tests asserting:

```python
with patch.object(postprocessing, "VisionDescriber") as vision_cls:
    ...
    vision_cls.assert_not_called()
```

If `VisionDescriber` remains constructed for dependency compatibility, assert instead that `describe` is never called and that the enhancer was created with `allow_vision_fill=False`; prefer not constructing it when the report path no longer uses it.

- [ ] **Step 8: Run image tests and fix only integration regressions**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_postprocessing.py tests/images/test_enhancer.py tests/images/test_bank.py tests/images/test_relevance.py -q
```

Expected: PASS. Any failure should be limited to fixtures whose alt lacks a named entity or whose report context does not mention the fixture entity; update those fixtures rather than weakening the gate.

- [ ] **Step 9: Commit the pipeline integration**

```bash
git add src/local_deep_research/images/postprocessing.py src/local_deep_research/images/enhancer.py tests/images/test_postprocessing.py tests/images/test_enhancer.py
 git commit -m "feat(images): enforce strict entity gate and disable fallback"
```

---

### Task 4: Add reason-coded observability and regression coverage

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py`
- Modify: `tests/images/test_postprocessing.py`
- Modify: `tests/images/test_relevance.py`

**Interfaces:**
- Consumes: `ImageRelevanceDecision` objects from Task 2.
- Produces: one aggregate `[IMG-TRACE] ENTITY_GATE` event, one `[IMG-TRACE] ELIGIBLE_BANK` event, and one `SECTION_FALLBACK status=disabled` event per image-enabled run.

- [ ] **Step 1: Write failing logging assertions**

```python
def test_entity_gate_logs_reason_counts(caplog):
    # Use the existing Loguru capture fixture/helper used by this repository.
    run_image_postprocessing_with_one_kept_and_one_dropped_candidate()
    text = captured_log_text()
    assert "[IMG-TRACE] ENTITY_GATE" in text
    assert "keep_context_match=" in text
    assert "drop_no_named_entity=" in text
    assert "[IMG-TRACE] ELIGIBLE_BANK" in text
    assert "[IMG-TRACE] SECTION_FALLBACK" in text
    assert "status=disabled" in text
```

- [ ] **Step 2: Run the focused logging test and verify it fails**

Run:

```bash
PYTHONPATH=src pytest tests/images/test_postprocessing.py::test_entity_gate_logs_reason_counts -q
```

Expected: FAIL because the new events are not emitted yet.

- [ ] **Step 3: Implement deterministic reason aggregation**

Use a `Counter` keyed by `decision.reason`. Emit stable fields in a fixed order:

```text
keep_context_match
keep_context_rescue
drop_missing_alt
drop_no_named_entity
drop_entity_extraction_failed
drop_foreign_entity_conflict
drop_unrelated_named_entity
drop_unresolved_entity_relation
drop_source_url_not_cited
drop_context_build_failed
```

`keep_context_rescue` is emitted for log stability but is always zero in this revision (the rescue path was removed; see the hard Step 5 same-origin check). Omit or emit zero consistently; prefer emitting all fields so run comparisons are stable.

- [ ] **Step 4: Add regression cases for aggregation-page pollution and uncited source URLs**

Add tests that assert:

```python
# Related source page, unrelated page-level candidate.
assert evaluate_candidate(
    image(alt="重庆洪崖洞旅游攻略", source="https://instagram.example/popular/广州景点"),
    context,
).status == "drop"

# Uncited source URL, explicitly context-confirmed entity -> hard drop.
assert evaluate_candidate(
    image(alt="中山纪念堂", source="https://generic-gallery.example/item"),
    context_with_explicit_sun_yat_sen_memorial_relation,
).reason == "source_url_not_cited"
```

- [ ] **Step 5: Run the complete image test suite**

Run:

```bash
PYTHONPATH=src pytest tests/images -q
```

Expected: PASS with no section fallback or Vision-admission regression.

- [ ] **Step 6: Commit observability and regression coverage**

```bash
git add src/local_deep_research/images/postprocessing.py tests/images/test_postprocessing.py tests/images/test_relevance.py
 git commit -m "test(images): cover strict entity gate decisions"
```

---

### Task 5: Full verification and plan acceptance review

**Files:**
- No product files expected.
- Verify: all files changed by Tasks 1-4.

- [ ] **Step 1: Run targeted static checks**

Run:

```bash
ruff check src/local_deep_research/images tests/images
python -m compileall -q src/local_deep_research/images
```

Expected: both commands exit 0.

- [ ] **Step 2: Run the complete image test suite again**

Run:

```bash
PYTHONPATH=src pytest tests/images -q
```

Expected: all tests pass.

- [ ] **Step 3: Search for forbidden production paths**

Run:

```bash
grep -R "fill_section_images(enhanced\|_vision_fill(bank" -n src/local_deep_research/images
```

Expected: no production call from `postprocessing.py`; a guarded helper definition may remain only if retained for compatibility.

- [ ] **Step 4: Review the final diff against the approved spec**

Run:

```bash
git diff --stat HEAD~4..HEAD
git diff --check HEAD~4..HEAD
```

Confirm manually:

- no Vision admission path remains;
- no raw-bank fallback path remains;
- no generic alt passes the first gate;
- unresolved entity relations drop;
- an uncited `source_url` rejects a context-confirmed entity with `source_url_not_cited`;
- persistence and URL rewriting are unchanged.

- [ ] **Step 5: Run the broader relevant tests**

Run:

```bash
PYTHONPATH=src pytest tests/images tests/research_library/downloaders -q
```

Expected: PASS. If unrelated pre-existing failures occur, report the exact failing tests and do not mark the feature verified.

- [ ] **Step 6: Commit only if all verification passes**

```bash
git status --short
git log --oneline -5
```

Do not include the existing unrelated untracked plan files in any feature commit.

---

## Self-Review Checklist

- [x] Spec coverage: hard alt gate, named entities, current-run context, hard source-URL citation, conflict rejection, fail-closed unresolved cases, no Vision, one Eligible Bank, fallback disabled, logs, tests, and acceptance criteria each have a task.
- [x] No external NER or knowledge-base dependency is introduced.
- [x] Later tasks use the exact interfaces introduced earlier (`ImageBank.subset`, `ReportEntityContext`, `ImageRelevanceDecision`, `build_report_entity_context`, `evaluate_candidate`).
- [x] No task asks the implementer to guess an error-handling policy; failure reasons and outcomes are specified.
- [x] Existing persistence, resizing, database, and URL rewrite behavior remains outside the change scope.
- [x] Existing unrelated untracked plan files are explicitly excluded from commits.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-25-strict-context-entity-image-gate.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with review checkpoints.
2. **Inline Execution** — execute tasks in this session using the executing-plans workflow.
