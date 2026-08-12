# Deferred Image Fill — Attach Asymmetry Fix + Diagnostic Probes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deferred image-fill write to the same container set `build_citation_index` reads, so cited URLs that survive only in `all_links_of_system` get their fetched images attached instead of silently producing `filled=0/N` + `BANK_EMPTY`.

**Architecture:** Two coupled one-line-class changes plus one observability event. (1) The attach loop in `_deferred_image_fill` also writes `html_content` into `results["all_links_of_system"]` records. (2) Both call sites pass the *same* `results_for_fill` dict to `enhance_report_with_images` that they pass to `_deferred_image_fill`, so the injected `all_links_of_system` key is visible downstream. (3) A new `ATTACH_MISS` IMG-TRACE event records every cited URL that matched no record, so this class of failure is self-diagnosing next time.

**Tech Stack:** Python 3.14, pytest 9, loguru, uv-managed venv.

## Global Constraints

- Branch: `main` is the only active branch. Run `git rev-parse --abbrev-ref HEAD` before every commit; if it does not print `main`, STOP.
- No background git. All git operations foreground/blocking only.
- After every commit run `git log --oneline -3` and confirm the new commit is at HEAD on `main`.
- Test command (host, uv venv — the container image has no pytest and must NOT be mutated):
  `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest <paths> -q`
  If `.venv` is missing, create it once with `uv sync --group dev`.
- Surgical changes only: touch only the lines these tasks name. Do not reformat, rename, or "improve" adjacent code.
- Existing IMG-TRACE field vocabulary is fixed: per-image events carry `img_alt`, `img_url`, `img_source_url`, `cite_num`, `ref_url`. New events must reuse these names, not invent synonyms.
- Deployment: source is hot-mounted read-only into the container at
  `/install/.venv/lib/python3.14/site-packages/local_deep_research/`. Source edits apply on container restart; no image rebuild needed.

---

## Background: the defect, in one paragraph

`build_citation_index` (`src/local_deep_research/images/relevance.py:664-683`) builds `url_to_html` from **both** `results["findings"][].search_results[]` **and** `results["all_links_of_system"]`. The attach loop in `_deferred_image_fill` (`src/local_deep_research/web/services/research_service.py:689-698`) writes to **only** `findings[].search_results[]`. In detailed mode `collector.reset()` clears `_results` between subsections, so cross-subsection cited URLs survive only in `all_links_of_system` — the attach loop finds no matching record, `attached` stays `False` for every URL, and nothing is written. `_inject_all_links_of_system` (lines 440-463) was added to fix the **read** side of exactly this problem; the **write** side was never updated. Verified on research `a6e77742-a420-4f38-904c-12baec097303` (2026-08-07): 2919 images fetched across 43 cited URLs, `filled=0/77`, 0/43 matches.

**Why the write-side fix alone is insufficient (verified by reproduction):** `_inject_all_links_of_system` returns a *new* dict (`merged = dict(results)`) and sets `merged["all_links_of_system"]`. The original `results` never gains that key. Both call sites pass `results_for_fill` to the fill but the original `results` to `enhance_report_with_images`. So writing into `all_links_of_system` records is invisible downstream unless the same dict is passed onward. Both parts are required; each alone still yields `BANK_EMPTY`.

**Not a defect (previously suspected, now retracted):** `CITATION_INDEX nums=77` is `len(num_to_url)` (`postprocessing.py:242`), a count — not a maximum. Citation numbering is sparse (1..92 with 77 distinct keys), and `_url_to_cite_num` is derived from `num_to_url` (`research_service.py:569-571`), so every emitted `cite_num` is a valid key by construction. Do not "fix" cite numbering.

**Expected yield after the fix:** unblocking attachment does not mean all 2919 images appear. `postprocessing.py:333-336` skips candidates with empty alt before scoring, and only 367/2919 (12.6%) of the fetched images had non-empty alt. Treat "images appear at all" as success, not a specific count.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/local_deep_research/web/services/research_service.py` | deferred fill + call sites | Modify: attach loop (~689-698), add ATTACH_MISS event, two call sites (~1585-1609, ~1985-2010) |
| `tests/web/test_deferred_image_fill.py` | deferred fill unit tests | Modify: add post-`reset()` regression tests |
| `tests/images/test_citation_index.py` | index read-surface tests | Unchanged — read here for existing fixture conventions |

**Why the existing tests missed this:** the helper `_make_results` (`tests/web/test_deferred_image_fill.py:38-55`) always places cited URLs *into* `findings[].search_results[]` — the one shape where the bug cannot manifest. All existing tests in that file pass against the broken code. Task 1 adds the missing shape.

**Plan pre-validated by dry run (2026-08-07).** Before this plan was written, Task 1's test and Task 2's patch were applied to a scratch copy of the tree and then reverted. Confirmed empirically:
- Task 1's test fails against current `main` with exactly `assert 0 == 1` on `assert filled == 1`.
- Task 2's patch makes it pass.
- `tests/web/test_deferred_image_fill.py tests/images/` → **336 passed, 1 skipped**, no regressions.

The working tree was restored afterwards; this plan is the only artifact. Task 3's call-site change and Task 4's probe were verified by reproduction script rather than by patching the tree, so treat their expected results as predicted, not measured.

---

### Task 1: Regression test — cited URL present only in `all_links_of_system`

Write the failing test first. It must fail against current `main`, proving it reproduces the production defect.

**Files:**
- Modify: `tests/web/test_deferred_image_fill.py`

**Interfaces:**
- Consumes: `_deferred_image_fill(research_id, *, final_markdown, results, settings_snapshot, progress_callback=None) -> int` from `local_deep_research.web.services.research_service`; helper `_extracted_image(url, alt, source_url)` already defined at line 25 of this test file.
- Produces: test names `test_attaches_when_url_only_in_all_links_of_system` and `test_all_links_html_visible_to_build_citation_index`, relied on by Task 2's verification step.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_deferred_image_fill.py`:

```python
class TestDeferredFillPostResetShape:
    """Detailed mode: ``collector.reset()`` clears ``_results`` between
    subsections, so a cross-subsection cited URL survives ONLY in
    ``all_links_of_system``. The attach loop must write there too, or
    ``filled`` stays 0 and ``build_citation_index`` sees an empty map.

    Regression for research a6e77742 (2026-08-07): 2919 images fetched,
    filled=0/77, BANK_EMPTY reason=no_citations_or_html.
    """

    CITED = "https://www.chinadiscovery.com/shanghai/zhujiajiao.html"

    def _markdown(self):
        return (
            "## 朱家角古镇\n\n"
            f"江南水乡 [[56]]({self.CITED})。\n\n"
            "## Sources\n\n"
            "[56, 1224] Zhujiajiao Ancient Town\n"
            f"   URL: {self.CITED}\n"
        )

    def _results_post_reset(self):
        """findings[] holds only the LAST subsection (a different URL);
        the cited URL lives in the cumulative all_links_of_system list."""
        return {
            "findings": [
                {"search_results": [{"link": "https://other.example/last"}]}
            ],
            "all_links_of_system": [{"link": self.CITED}],
        }

    def test_attaches_when_url_only_in_all_links_of_system(self):
        results = self._results_post_reset()
        fetched = {self.CITED: {"text": "t", "images": [_extracted_image(
            url="https://img/z.jpg", alt="放生桥", source_url=self.CITED)]}}

        with patch(
            "local_deep_research.research_library.downloaders.extraction."
            "pipeline.fetch_content_with_images",
            return_value=fetched,
        ):
            filled = _deferred_image_fill(
                "res-post-reset",
                final_markdown=self._markdown(),
                results=results,
                settings_snapshot={"report.enable_images": True},
            )

        assert filled == 1, (
            "attach loop must match the cited URL inside "
            "all_links_of_system, not only findings[].search_results[]"
        )
        record = results["all_links_of_system"][0]
        assert record.get("html_content"), (
            "html_content must be written onto the all_links_of_system record"
        )

    def test_all_links_html_visible_to_build_citation_index(self):
        """End-to-end read check: what the fill writes, the index must see."""
        from local_deep_research.images.relevance import build_citation_index

        results = self._results_post_reset()
        fetched = {self.CITED: {"text": "t", "images": [_extracted_image(
            url="https://img/z.jpg", alt="放生桥", source_url=self.CITED)]}}

        with patch(
            "local_deep_research.research_library.downloaders.extraction."
            "pipeline.fetch_content_with_images",
            return_value=fetched,
        ):
            _deferred_image_fill(
                "res-post-reset-2",
                final_markdown=self._markdown(),
                results=results,
                settings_snapshot={"report.enable_images": True},
            )

        num_to_url, _sections, url_to_html = build_citation_index(
            self._markdown(), results
        )
        assert num_to_url, "citation index should parse the Sources block"
        assert url_to_html, (
            "url_to_html must be non-empty — an empty map is exactly the "
            "BANK_EMPTY reason=no_citations_or_html production signature"
        )
        assert self.CITED in url_to_html
```

Note on conventions, both verified against the existing file: images-enabled is supplied via `settings_snapshot={"report.enable_images": True}` (the import of `get_setting_from_snapshot` is function-local at `research_service.py:534`, so patching it on the module would not take effect — the existing 20 tests never patch it). The fetch stub returns a dict keyed by URL whose value carries `text` and `images`.

- [ ] **Step 2: Run the tests to verify they FAIL**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::TestDeferredFillPostResetShape -q
```
Expected: 2 failed. First failure is `assert filled == 1` receiving `0`. This is the production defect reproduced.

- [ ] **Step 3: Commit the failing tests**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add tests/web/test_deferred_image_fill.py
git commit -m "test(images): reproduce deferred-fill attach miss for post-reset URL shape"
git log --oneline -3
```

---

### Task 2: Fix the attach loop to write `all_links_of_system`

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py:689-698`

**Interfaces:**
- Consumes: local `payload` (JSON string from `dumps_images`), local `url` (the cited URL) — both already in scope in the fill loop.
- Produces: `attached: bool` semantics unchanged (True if any record received the payload); `filled` counter semantics unchanged.

- [ ] **Step 1: Replace the attach loop**

Current code at `research_service.py:689-698`:

```python
        attached = False
        for finding in results.get("findings", []) or []:
            for sr in finding.get("search_results", []) or []:
                sr_url = sr.get("url") or sr.get("link") or ""
                if sr_url != url:
                    continue
                sr["html_content"] = payload
                attached = True
```

Replace with:

```python
        attached = False
        for finding in results.get("findings", []) or []:
            for sr in finding.get("search_results", []) or []:
                sr_url = sr.get("url") or sr.get("link") or ""
                if sr_url != url:
                    continue
                sr["html_content"] = payload
                attached = True
        # Also write the cumulative cross-subsection list. In detailed
        # mode ``collector.reset()`` clears ``_results`` between
        # subsections, so a cited URL often survives ONLY here.
        # ``build_citation_index`` already READS this list (relevance.py
        # "Merge in the cross-subsection cumulative list (fix #1+#6)");
        # writing it keeps the read and write surfaces symmetric.
        for record in results.get("all_links_of_system") or []:
            rec_url = record.get("link") or record.get("url") or ""
            if rec_url != url:
                continue
            record["html_content"] = payload
            attached = True
```

- [ ] **Step 2: Run the Task 1 tests**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::TestDeferredFillPostResetShape -q
```
Expected: `test_attaches_when_url_only_in_all_links_of_system` PASSES.
`test_all_links_html_visible_to_build_citation_index` also PASSES here, because this test passes one dict to both stages. Task 3 covers the call-site case where they differ.

- [ ] **Step 3: Run the full image + fill suites for regressions**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py tests/images/ -q
```
Expected: all pass (20 pre-existing in the fill file + the 2 new, plus the `tests/images/` suite). If any pre-existing test fails, STOP and report — do not adjust the test to fit the change.

- [ ] **Step 4: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py
git commit -m "fix(images): write html_content to all_links_of_system in deferred fill

build_citation_index reads both findings[].search_results[] and
all_links_of_system, but the attach loop wrote only the former. In
detailed mode collector.reset() clears _results between subsections,
so cross-subsection cited URLs survive only in all_links_of_system —
producing filled=0/N and BANK_EMPTY despite successful fetches."
git log --oneline -3
```

---

### Task 3: Pass the same dict to the fill and to postprocessing

Without this, Task 2's write is invisible downstream: `_inject_all_links_of_system` returns a new dict, and the original `results` never gains the `all_links_of_system` key.

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py` — quick mode ~1585-1609, detailed mode ~1985-2010
- Modify: `tests/web/test_deferred_image_fill.py`

**Interfaces:**
- Consumes: `_inject_all_links_of_system(results, system) -> dict` (line 440); `enhance_report_with_images(*, research_id, clean_markdown, results, db_session, **img_args) -> str`.
- Produces: no new symbols. Behavioural contract: the dict handed to `enhance_report_with_images` is the same object handed to `_deferred_image_fill`.

- [ ] **Step 1: Write the failing call-site test**

Append to `tests/web/test_deferred_image_fill.py`:

```python
def test_inject_returns_new_dict_original_lacks_key():
    """Guards the call-site contract.

    ``_inject_all_links_of_system`` returns a NEW dict; the original
    ``results`` never gains ``all_links_of_system``. So passing
    ``results_for_fill`` to the fill but the original ``results`` to
    ``enhance_report_with_images`` hides everything the fill wrote
    into the cumulative list. Both stages must receive the same dict.
    """
    from local_deep_research.web.services.research_service import (
        _inject_all_links_of_system,
    )

    class _Sys:
        all_links_of_system = [{"link": "https://cited.example/page"}]

    results = {"findings": []}
    merged = _inject_all_links_of_system(results, _Sys())

    assert "all_links_of_system" in merged
    assert "all_links_of_system" not in results, (
        "original results must NOT gain the key — this is precisely why "
        "both stages have to be handed `merged`, not `results`"
    )
    assert merged["all_links_of_system"][0] is _Sys.all_links_of_system[0], (
        "record objects are shared, so writes through `merged` are visible "
        "to any holder of the same record"
    )
```

- [ ] **Step 2: Run it**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::test_inject_returns_new_dict_original_lacks_key -q
```
Expected: PASS immediately. This test documents existing behaviour of the helper — it is the *rationale* for the call-site edit, and it must keep passing after it.

- [ ] **Step 3: Fix the quick-mode call site**

At `research_service.py` ~1585-1609, the fill receives `results_for_fill` but the enhance call receives `results`. Change only the `results=` argument of the `enhance_report_with_images(...)` call:

```python
                                    clean_markdown = enhance_report_with_images(
                                        research_id=research_id,
                                        clean_markdown=clean_markdown,
                                        results=results_for_fill,
                                        db_session=img_db_session,
                                        **img_args,
                                    )
```

- [ ] **Step 4: Fix the detailed-mode call site**

At `research_service.py` ~1985-2010, same single-argument change:

```python
                            final_report["content"] = (
                                enhance_report_with_images(
                                    research_id=research_id,
                                    clean_markdown=final_report["content"],
                                    results=results_for_fill,
                                    db_session=img_db_session,
                                    **img_args,
                                )
                            )
```

- [ ] **Step 5: Verify both call sites changed and nothing else did**

Run:
```bash
git diff -U2 src/local_deep_research/web/services/research_service.py
```
Expected: exactly two changed lines in this task, both `results=results` → `results=results_for_fill`, inside `enhance_report_with_images(...)` calls. `_deferred_image_fill(...)` call sites must still read `results=results_for_fill` (unchanged). If the diff shows anything else, revert and redo.

- [ ] **Step 6: Run the full suites**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/ tests/images/ -q
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py tests/web/test_deferred_image_fill.py
git commit -m "fix(images): hand enhance_report_with_images the injected results dict

_inject_all_links_of_system returns a new dict; the original results
never gains all_links_of_system. Passing the original downstream hid
everything the deferred fill wrote into the cumulative list, so
url_to_html stayed empty and BANK_EMPTY fired."
git log --oneline -3
```

---

### Task 4: Probe — `ATTACH_MISS` event

Makes this failure class self-diagnosing. Without it, a future recurrence again costs a full log forensics pass.

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py` (attach loop, after Task 2's edit)
- Modify: `tests/web/test_deferred_image_fill.py`

**Interfaces:**
- Consumes: `attached: bool`, `url`, `cite_num_for_url` (assigned at `research_service.py:672`), `research_id` — all in scope.
- Produces: log line `[IMG-TRACE] ATTACH_MISS research=<id> cite_num=<n> ref_url=<url> findings_scanned=<int> all_links_scanned=<int>`.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_deferred_image_fill.py`:

```python
def test_attach_miss_event_emitted_when_no_record_matches(loguru_caplog):
    """A cited URL matching no record must announce itself.

    Silence here is what made research a6e77742 cost a full forensic
    pass: filled=0/77 with no per-URL reason.
    """
    cited = "https://orphan.example/never-in-results"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7, 1] Orphan\n"
        f"   URL: {cited}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/o.jpg", alt="orphan", source_url=cited)]}}

    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images",
        return_value=fetched,
    ):
        filled = _deferred_image_fill(
            "res-miss",
            final_markdown=markdown,
            results=results,
            settings_snapshot={"report.enable_images": True},
        )

    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 0
    assert "ATTACH_MISS" in text
    assert cited in text
```

Note: this project logs via loguru, which does not reach plain `caplog`. Use the `loguru_caplog` fixture (defined at `tests/conftest.py:604`) and join records exactly as `tests/images/test_img_trace_audit_events.py:25-26` does — the same `_records_text` pattern is inlined above.

- [ ] **Step 2: Run it to verify it FAILS**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::test_attach_miss_event_emitted_when_no_record_matches -q
```
Expected: FAIL on `assert "ATTACH_MISS" in text`.

- [ ] **Step 3: Add the probe**

In the attach loop, replace the counters and add the miss branch. After Task 2 the loop ends with `attached = True` inside the `all_links_of_system` loop; extend it to count scans and log misses:

```python
        attached = False
        findings_scanned = 0
        for finding in results.get("findings", []) or []:
            for sr in finding.get("search_results", []) or []:
                findings_scanned += 1
                sr_url = sr.get("url") or sr.get("link") or ""
                if sr_url != url:
                    continue
                sr["html_content"] = payload
                attached = True
        # Also write the cumulative cross-subsection list. In detailed
        # mode ``collector.reset()`` clears ``_results`` between
        # subsections, so a cited URL often survives ONLY here.
        # ``build_citation_index`` already READS this list (relevance.py
        # "Merge in the cross-subsection cumulative list (fix #1+#6)");
        # writing it keeps the read and write surfaces symmetric.
        all_links_scanned = 0
        for record in results.get("all_links_of_system") or []:
            all_links_scanned += 1
            rec_url = record.get("link") or record.get("url") or ""
            if rec_url != url:
                continue
            record["html_content"] = payload
            attached = True
        if not attached:
            # Fetched images with nowhere to put them. Records the
            # candidate-set sizes so a reader can tell "no records at
            # all" from "records present but no URL matched".
            logger.info(
                f"[IMG-TRACE] ATTACH_MISS research={research_id} "
                f"cite_num={cite_num_for_url} "
                f"ref_url={url} "
                f"findings_scanned={findings_scanned} "
                f"all_links_scanned={all_links_scanned}"
            )
```

- [ ] **Step 4: Run the test**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::test_attach_miss_event_emitted_when_no_record_matches -q
```
Expected: PASS.

- [ ] **Step 5: Full suite**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/ tests/images/ -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py tests/web/test_deferred_image_fill.py
git commit -m "feat(observability): add IMG-TRACE ATTACH_MISS for unattachable fetched images

Records cite_num, ref_url and both candidate-set sizes when a fetched
URL matches no record, so filled=0/N reports its own reason instead of
requiring a full log forensics pass."
git log --oneline -3
```

---

### Task 5: Live verification against the container

Tests prove the units; this proves the deployed pipeline. Per the project verification rule: a change to product source has a runtime surface, so drive it.

**Files:** none modified.

- [ ] **Step 1: Restart the container to pick up the hot-mounted source**

```bash
docker compose -f <the compose file that defines ldr-local> up -d local-deep-research
```
Confirm the running module matches the repo (hot mount, not a stale image):
```bash
docker exec ldr-local md5sum \
  /install/.venv/lib/python3.14/site-packages/local_deep_research/web/services/research_service.py
md5sum src/local_deep_research/web/services/research_service.py
```
Expected: identical hashes. Expect a brief I/O burst and ~36s to healthy — normal, not a fault.

- [ ] **Step 2: Run a small detailed-mode research with images enabled**

Use the WebUI. Keep it small (2-3 subsections) — the reference run took 40 minutes at 9 subsections. Detailed mode is required: quick mode does not exercise the per-subsection `reset()` that creates the failing shape.

- [ ] **Step 3: Read the trace**

```bash
docker logs ldr-local --since <run-start> 2>&1 | grep -E \
  'DEFERRED_FILL|CITATION_INDEX|BANK_EMPTY|ATTACH_MISS|IMG-TRACE\] END'
```

Success criteria:
- `DEFERRED_FILL ... done filled=N/M` with **N > 0**
- `CITATION_INDEX ... html_covered=` **> 0**
- **no** `BANK_EMPTY reason=no_citations_or_html`
- `END ... status=` not `empty`

Interpreting `ATTACH_MISS` lines if they appear:
- `findings_scanned=0 all_links_scanned=0` → no candidate records at all; a different upstream problem, not this fix.
- both counts > 0 with misses → genuine URL string drift (trailing slash, fragment, percent-encoding). That is a *separate* defect needing normalization; file it, do not widen this change.

- [ ] **Step 4: Confirm images render**

Open the report in the WebUI and confirm images appear in section bodies. Do not infer success from logs alone.

Expect a modest count. Empty-alt candidates are dropped before scoring (`postprocessing.py:333-336`), and in the reference run only 12.6% of fetched images had alt text. "Some images present" is the bar; a specific count is not.

- [ ] **Step 5: Record the outcome**

If all criteria pass, report the actual numbers (`filled=N/M`, `html_covered=`, rendered image count). If any fail, report the exact log lines — do not claim success.

---

## Self-Review

**Spec coverage:** Write-side asymmetry → Task 2. Call-site dict mismatch → Task 3. Probe → Task 4. Regression coverage for the shape the old tests missed → Task 1. Live verification → Task 5. The retracted `cite_num>77` claim is documented in Background as explicitly *not* to be actioned.

**Placeholders:** none — every code step carries literal code; every test step carries a runnable command with an expected result.

**Type consistency:** `attached: bool`, `filled: int`, `payload: str`, `cite_num_for_url: str` used consistently. Task 4's code block reproduces Task 2's loop in full (rather than saying "as in Task 2") because tasks may be executed out of order by separate agents.

**Known gap, deliberately out of scope:** if `ATTACH_MISS` shows records present but no URL matching, that is URL normalization — a distinct defect. `_normalize_url` exists at `relevance.py:214-215` and is unused on this path. Left for a follow-up plan rather than widened into this one.
