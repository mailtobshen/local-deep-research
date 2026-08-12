# Canonical URL Attach in Deferred Fill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deferred image-fill attach loop treat canonical-equal URLs (trailing-slash, Steam `filedetails/?id` separator) as hits — exact match first, else first canonical-equal record — so same-origin citations attach instead of silently becoming `ATTACH_MISS`.

**Architecture:** One coupled change in `_deferred_image_fill`'s attach block (`research_service.py:760-822`). The existing observe-only `ATTACH_NEAR_MATCH` scan already finds the first canonical-equal candidate; promote it: when no exact match exists and a canonical-equal record is found, write `html_content` onto that record, set `attached=True`, and emit a new `ATTACH_CANONICAL` probe (reusing `_classify_url_diff`'s `via=` vocabulary). `filled += 1` stays gated solely on `attached`, so a citation counts at most once. `_canonicalize_url` and `_classify_url_diff` are consumed unchanged.

**Tech Stack:** Python 3.14, pytest 9, loguru, uv-managed venv.

## Global Constraints

- Branch: `main` is the only active branch. Run `git rev-parse --abbrev-ref HEAD` before every commit; if it does not print `main`, STOP.
- No background git. All git operations foreground/blocking only.
- After every commit run `git log --oneline -3` and confirm the new commit is at HEAD on `main`.
- Test command (host, uv venv — the container image has no pytest and must NOT be mutated):
  `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest <paths> -q`
  If `.venv` is missing, create it once with `uv sync --group dev`.
- Surgical changes only: touch only the lines these tasks name. Do not reformat, rename, or "improve" adjacent code.
- IMG-TRACE field vocabulary is fixed: per-citation events carry `cite_num`, `ref_url`. The new `ATTACH_CANONICAL` event reuses these names plus `record_url` and `via=`. Do not invent synonyms.
- Two existing tests encode the CURRENT (pre-fix) behavior and must be updated, not left to fail:
  - `test_attach_near_match_emitted_on_trailing_slash` (tests/web/test_deferred_image_fill.py:766) currently asserts `filled == 0` + `ATTACH_NEAR_MATCH`. Under the fix its expectation FLIPS to `filled == 1` + `ATTACH_CANONICAL`. Task 2 does this.
  - `test_no_attach_near_match_when_query_differs` (tests/web/test_deferred_image_fill.py:803) stays valid unchanged — it is the anti-mismatch red line (`?id=1` vs `?id=2` must not merge).
- Deployment: source is hot-mounted read-only into the container at `/install/.venv/lib/python3.14/site-packages/local_deep_research/`. Source edits apply on container restart; no image rebuild.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/local_deep_research/web/services/research_service.py` | deferred fill attach block (~760-822) | Modify: add canonical pass + `ATTACH_CANONICAL` probe |
| `tests/web/test_deferred_image_fill.py` | deferred fill unit tests | Modify: flip the trailing-slash test; add Steam, exact-precedence, five-key, miss-still-fires tests |

**Why the existing tests look the way they do:** `test_attach_near_match_emitted_on_trailing_slash` (line 766) was written to lock in the observe-only NEAR_MATCH behavior added in commit `0b1733b4` — at the time, same-origin detection was deliberately NOT wired to attach (the "Known gap" the spec supersedes). That decision is now reversed, so the test's expectation reverses with it. Everything else in the file (the 20+ end-to-end tests, the blocklist tests, the miss test at 634) is unaffected because their fixtures use either exact-match URLs or URLs with no record at all.

---

### Task 1: Failing tests — flip trailing-slash expectation + add canonical cases

Write/update the tests first. The flipped trailing-slash test and the Steam test MUST fail against current `main`, proving they reproduce the defect. The exact-precedence and miss-still-fires tests also fail until Task 2 lands.

**Files:**
- Modify: `tests/web/test_deferred_image_fill.py:766-800` (flip `test_attach_near_match_emitted_on_trailing_slash`)
- Modify: `tests/web/test_deferred_image_fill.py` (append 4 new tests after `test_no_attach_near_match_when_query_differs`, which ends at line 827)

**Interfaces:**
- Consumes: `_deferred_image_fill(research_id, *, final_markdown, results, settings_snapshot, progress_callback=None) -> int`; helper `_extracted_image(url, alt, source_url)` at line 26; `loguru_caplog` fixture (tests/conftest.py:604) used inside `with loguru_caplog.at_level(logging.INFO):` exactly as the file's other tests do.
- Produces: test names `test_canonical_attach_on_trailing_slash` (the renamed/flipped 766 test — see Step 1), `test_canonical_attach_steam_question_mark`, `test_exact_match_takes_precedence_over_canonical`, `test_attach_canonical_carries_five_key_fields`, `test_attach_miss_still_fires_with_no_near_neighbor`.

- [ ] **Step 1: Replace the trailing-slash test at 766-800**

The current test (lines 766-800) asserts the observe-only behavior. Replace the WHOLE function (from its `def` line through line 800) with the flipped expectation — rename it to reflect the new behavior. The replacement:

```python
def test_canonical_attach_on_trailing_slash(loguru_caplog):
    """A cited URL whose only record differs by a trailing slash must
    attach via canonical equality and announce ATTACH_CANONICAL with
    via=trailing_slash — no longer an observe-only near-match.

    Regression for the 2026-08-12 run c325e2a0: 17 trailing-slash
    citations were ATTACH_MISS despite successful fetches.
    """
    cited = "https://example.org/page"           # ref_url, no slash
    record_url = "https://example.org/page/"     # record side, slash
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": record_url}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-canon", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1, (
        "trailing-slash record must attach via canonical equality"
    )
    assert results["findings"][0]["search_results"][0].get("html_content"), (
        "html_content must be written onto the trailing-slash record"
    )
    assert "ATTACH_CANONICAL" in text
    assert cited in text
    assert record_url in text
    assert "via=trailing_slash" in text
    assert "ATTACH_MISS" not in text, (
        "a successfully-attached citation must not also emit ATTACH_MISS"
    )
    assert "ATTACH_NEAR_MATCH" not in text, (
        "a successfully-attached citation must not also emit the "
        "observe-only near-match probe"
    )
```

- [ ] **Step 2: Append the Steam `?id` test after line 827**

Append after `test_no_attach_near_match_when_query_differs` (ends line 827):

```python
def test_canonical_attach_steam_question_mark(loguru_caplog):
    """Steam's filedetails?id=<n> vs filedetails/?id=<n> (slash before
    the query separator) is canonical-equal and must attach.

    Regression for 2026-08-12 run c325e2a0: 5 Steam Workshop citations
    (cite_num 30/40/111/112/166-equivalent) were ATTACH_MISS via=trailing_slash.
    """
    cited = "https://steamcommunity.com/sharedfiles/filedetails?id=3506925216"
    record_url = "https://steamcommunity.com/sharedfiles/filedetails/?id=3506925216"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": record_url}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-steam", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1
    assert results["findings"][0]["search_results"][0].get("html_content")
    assert "ATTACH_CANONICAL" in text
    assert record_url in text
    assert "via=trailing_slash" in text
    assert "ATTACH_MISS" not in text
```

- [ ] **Step 3: Append the exact-precedence test**

Append after the Steam test:

```python
def test_exact_match_takes_precedence_over_canonical(loguru_caplog):
    """When records contain BOTH an exact match and a canonical
    near-neighbor, the exact record is written and ATTACH_CANONICAL
    does NOT fire. Exact wins; filled counts once.
    """
    cited = "https://example.org/page"
    exact_record = {"link": cited}
    slash_record = {"link": "https://example.org/page/"}
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    # Put the slash record first to prove precedence is not just
    # "first record wins" — exact must win regardless of order.
    results = {
        "findings": [{"search_results": [slash_record, exact_record]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-prec", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 1
    assert exact_record.get("html_content"), (
        "the exact-match record must receive html_content"
    )
    assert "ATTACH_CANONICAL" not in text, (
        "exact match must not trigger the canonical-attach probe"
    )
```

- [ ] **Step 4: Append the five-key-fields audit test**

Append after the precedence test:

```python
def test_attach_canonical_carries_five_key_fields(loguru_caplog):
    """ATTACH_CANONICAL must carry cite_num and ref_url (the five-key
    IMG-TRACE vocabulary), so a single grep reconstructs provenance —
    same audit pattern as tests/images/test_img_trace_audit_events.py.
    """
    cited = "https://example.org/page"
    markdown = (
        "## S\n\n"
        f"x [[42]]({cited})。\n\n"
        "## Sources\n\n"
        "[42] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {
        "findings": [{"search_results": [{"link": "https://example.org/page/"}]}],
        "all_links_of_system": [],
    }
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            _deferred_image_fill(
                "res-fields", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    canon_lines = [l for l in text.splitlines() if "ATTACH_CANONICAL" in l]
    assert canon_lines, "expected an ATTACH_CANONICAL line"
    line = canon_lines[0]
    assert "cite_num=42" in line
    assert f"ref_url={cited}" in line
```

- [ ] **Step 5: Append the miss-still-fires test**

The genuine-no-match path must still emit `ATTACH_MISS` and the observe-only `ATTACH_NEAR_MATCH` (no canonical neighbor exists). Append:

```python
def test_attach_miss_still_fires_with_no_near_neighbor(loguru_caplog):
    """A cited URL with no record at all — neither exact nor canonical —
    must still emit ATTACH_MISS. Guards that the canonical pass did not
    accidentally swallow the genuine-miss path.
    """
    cited = "https://orphan.example/never-in-results"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n"
        "[7] Ex\n"
        f"   URL: {cited}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/o.jpg", alt="orphan", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            filled = _deferred_image_fill(
                "res-miss2", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 0
    assert "ATTACH_MISS" in text
    assert "ATTACH_CANONICAL" not in text
```

- [ ] **Step 6: Run the new/changed tests to verify they FAIL**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::test_canonical_attach_on_trailing_slash \
  tests/web/test_deferred_image_fill.py::test_canonical_attach_steam_question_mark \
  tests/web/test_deferred_image_fill.py::test_exact_match_takes_precedence_over_canonical \
  tests/web/test_deferred_image_fill.py::test_attach_canonical_carries_five_key_fields \
  tests/web/test_deferred_image_fill.py::test_attach_miss_still_fires_with_no_near_neighbor \
  -q
```
Expected: 4 FAIL (the trailing-slash, Steam, exact-precedence, and five-key tests — all assert canonical attach which does not exist yet) and 1 PASS (`test_attach_miss_still_fires_with_no_near_neighbor` — the genuine-miss path already works). If `test_attach_miss_still_fires_with_no_near_neighbor` fails, STOP and report — the genuine-miss path is already broken independently.

- [ ] **Step 7: Commit the failing tests**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add tests/web/test_deferred_image_fill.py
git commit -m "test(images): canonical-equal URLs should attach in deferred fill

Flip test_attach_near_match_emitted_on_trailing_slash (observe-only)
to assert attach + ATTACH_CANONICAL. Add Steam filedetails/?id,
exact-precedence, five-key-fields, and genuine-miss-still-fires
cases. 4 fail against current main, reproducing the 2026-08-12
run c325e2a0 trailing-slash attach miss."
git log --oneline -3
```

---

### Task 2: Promote canonical equality to an attach criterion

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py:784-822`

**Interfaces:**
- Consumes: `_canonicalize_url(url) -> str` (imported locally at line 798; keep the local import), `_classify_url_diff(a, b) -> str` (module-level, line 489), `payload: str` and `url: str` already in scope.
- Produces: no new symbols. Behavioural contract: `attached` may now be set by the canonical pass; `filled += 1` is still gated solely on `attached`; new log event `ATTACH_CANONICAL`.

- [ ] **Step 1: Move the canonical import to the top of the attach block and add the canonical pass**

The current block at lines 784-822 is: the `if not attached:` `ATTACH_MISS` log, then the local import of `_canonicalize_url`, then the observe-only NEAR_MATCH scan. Replace lines 784-822 (the entire `if not attached:` block through the end of the `ATTACH_NEAR_MATCH` log) with:

```python
        if not attached:
            # Canonical pass: promote same-origin detection (previously
            # observe-only NEAR_MATCH) to an attach criterion. Only runs
            # when no exact match was found. First canonical-equal record
            # wins; ``filled`` still counts this citation at most once
            # because ``attached`` gates the ``filled += 1`` below.
            from ...images.relevance import _canonicalize_url
            ref_canon = _canonicalize_url(url)
            canonical_hit: str | None = None
            if ref_canon:
                for finding in results.get("findings", []) or []:
                    for sr in finding.get("search_results", []) or []:
                        cand = sr.get("url") or sr.get("link") or ""
                        if cand and cand != url and _canonicalize_url(cand) == ref_canon:
                            sr["html_content"] = payload
                            canonical_hit = cand
                            attached = True
                            break
                    if canonical_hit:
                        break
                if canonical_hit is None:
                    for record in results.get("all_links_of_system") or []:
                        cand = record.get("link") or record.get("url") or ""
                        if cand and cand != url and _canonicalize_url(cand) == ref_canon:
                            record["html_content"] = payload
                            canonical_hit = cand
                            attached = True
                            break
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
            # Observe-only diagnostic: was there a canonical
            # near-neighbor the canonical pass still refused? Under the
            # new same-origin-attach semantics this fires only for
            # exotic drifts the canonical rule does not cover.
            from ...images.relevance import _canonicalize_url
            ref_canon = _canonicalize_url(url)
            near_match: str | None = None
            if ref_canon:
                for finding in results.get("findings", []) or []:
                    for sr in finding.get("search_results", []) or []:
                        cand = sr.get("url") or sr.get("link") or ""
                        if cand and cand != url and _canonicalize_url(cand) == ref_canon:
                            near_match = cand
                            break
                    if near_match:
                        break
                if near_match is None:
                    for record in results.get("all_links_of_system") or []:
                        cand = record.get("link") or record.get("url") or ""
                        if cand and cand != url and _canonicalize_url(cand) == ref_canon:
                            near_match = cand
                            break
            if near_match is not None:
                via = _classify_url_diff(url, near_match)
                logger.info(
                    f"[IMG-TRACE] ATTACH_NEAR_MATCH research={research_id} "
                    f"cite_num={cite_num_for_url} ref_url={url} "
                    f"canonical_match_url={near_match} via={via}"
                )
```

Notes on the replacement (verified against current code):
- The canonical pass writes `html_content` and sets `attached=True` on the FIRST canonical-equal record it finds (findings surface first, then `all_links_of_system`), matching the "exact precedence; else first canonical" rule.
- `cand != url` is kept so an exact string match is never re-processed as canonical (harmless, but keeps the probe semantics clean).
- The local `from ...images.relevance import _canonicalize_url` now appears twice (once in the canonical pass, once in the observe-only probe). This duplicates the existing pattern (the original block already had this local import); leave both — do not hoist to module level, that is out of scope for this surgical change.
- The `ATTACH_NEAR_MATCH` observe-only probe is preserved verbatim, now under the `if not attached:` that follows the canonical pass — so it fires only when the canonical pass ALSO found nothing, which under the new semantics is rare (exotic non-canonical drift).

- [ ] **Step 2: Add the `ATTACH_CANONICAL` probe emission**

The `if attached:` block at line 823 currently starts with `filled += 1` then a comment and the `DEFERRED_FILLED` log. Insert the `ATTACH_CANONICAL` emission between `filled += 1` and the existing `DEFERRED_FILLED` comment block. Concretely, find this exact text at line 823-824:

```python
        if attached:
            filled += 1
            # Summary event — carries the full four-field vocabulary
```

Replace just those three lines with:

```python
        if attached:
            filled += 1
            if canonical_hit is not None:
                # Same-origin attach via canonical equality (exact match
                # found nothing). Records the record-side URL and the
                # classified raw difference so a reader can see why the
                # exact pass missed.
                via = _classify_url_diff(url, canonical_hit)
                logger.info(
                    f"[IMG-TRACE] ATTACH_CANONICAL research={research_id} "
                    f"cite_num={cite_num_for_url} ref_url={url} "
                    f"record_url={canonical_hit} via={via}"
                )
            # Summary event — carries the full four-field vocabulary
```

The `canonical_hit` variable is in scope here: it is assigned in the canonical pass above (defaulting to `None` when the exact pass already set `attached`). When the exact pass set `attached`, `canonical_hit` is `None` and this probe is skipped — that is the "exact match is silent" behavior.

- [ ] **Step 3: Run the Task 1 tests**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py::test_canonical_attach_on_trailing_slash \
  tests/web/test_deferred_image_fill.py::test_canonical_attach_steam_question_mark \
  tests/web/test_deferred_image_fill.py::test_exact_match_takes_precedence_over_canonical \
  tests/web/test_deferred_image_fill.py::test_attach_canonical_carries_five_key_fields \
  tests/web/test_deferred_image_fill.py::test_attach_miss_still_fires_with_no_near_neighbor \
  -q
```
Expected: all 5 PASS.

- [ ] **Step 4: Run the full image + fill suites for regressions**

Run:
```bash
LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest \
  tests/web/test_deferred_image_fill.py tests/images/ -q
```
Expected: all pass. Pay special attention to `test_attach_miss_event_emitted_when_no_record_matches` (line 634 — still valid, genuine miss), `test_no_attach_near_match_when_query_differs` (line 803 — anti-mismatch red line, still valid), and `test_attaches_when_url_only_in_all_links_of_system` (line 541 — exact match in `all_links_of_system`, unaffected). If any of these three fail, STOP and report — the canonical pass over-reached.

- [ ] **Step 5: Verify the diff is surgical**

Run:
```bash
git diff -U2 src/local_deep_research/web/services/research_service.py
```
Expected: changes confined to the ~784-850 region. The exact-match loop (762-783) must be byte-identical. No imports added at module top. If the diff shows anything outside the attach block, revert and redo.

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py
git commit -m "fix(images): attach canonical-equal URLs in deferred fill

The attach loop used strict string equality, so cited URLs that
differed from their record only by a trailing slash or the Steam
filedetails/?id separator were dropped as ATTACH_MISS even though
the NEAR_MATCH probe already detected them same-origin. Promote
that detection to an attach criterion: exact match first, else
first canonical-equal record. New ATTACH_CANONICAL probe (reuses
_classify_url_diff via= vocabulary). filled counts each citation
at most once. _canonicalize_url and _classify_url_diff unchanged."
git log --oneline -3
```

---

### Task 3: Live verification against the container

Tests prove the units; this proves the deployed pipeline. Per the project verification rule, a change to product source has a runtime surface, so drive it.

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
Expected: identical hashes. Expect a brief I/O burst and ~36s to healthy — normal, not a fault (see [[ldr-local-force-recreate-io-burst]]).

- [ ] **Step 2: Run a detailed-mode research with images enabled**

Use the WebUI. Detailed mode is required: quick mode does not exercise the per-subsection `reset()` that creates the cross-subsection URL shape. Keep the topic similar to the 2026-08-12 run (a Chinese-city travel guide) so the trailing-slash / Steam patterns recur.

- [ ] **Step 3: Read the trace**

```bash
docker logs ldr-local --since <run-start> 2>&1 | grep -E \
  'DEFERRED_FILL|CITATION_INDEX|BANK_EMPTY|ATTACH_MISS|ATTACH_CANONICAL|ATTACH_NEAR_MATCH|IMG-TRACE\] END'
```

Success criteria:
- `DEFERRED_FILL ... done filled=N/M` with **N** higher than the pre-fix baseline (the canonical citations now count).
- `CITATION_INDEX ... html_covered=` **higher** than before.
- `ATTACH_CANONICAL` lines appear, each with `via=trailing_slash` (or `www`/`scheme`/`combined`).
- `ATTACH_MISS` count **drops** toward the genuine-noise floor.
- **no** `BANK_EMPTY reason=no_citations_or_html`.
- `END ... status=ok`.

- [ ] **Step 4: Capture the run before the next container restart**

Per [[deferred-fill-attach-fix-verified]]: `docker logs` only goes back to the last container start. Capture this run's probe output to a persisted file immediately so it survives the next restart:
```bash
docker logs ldr-local --since <run-start> 2>&1 | grep -E 'IMG-TRACE' \
  > /tmp/canon-attach-verify-<research-id>.log
```

- [ ] **Step 5: Record the outcome**

Report the actual numbers (`filled=N/M`, `html_covered=`, `ATTACH_CANONICAL` count, `ATTACH_MISS` count). If any success criterion fails, report the exact log lines — do not claim success.

---

## Self-Review

**Spec coverage:**
- "Matching semantics: exact pass unchanged" → Task 2 Step 1 leaves lines 762-783 untouched, verified in Step 5.
- "Canonical pass only if not attached, first canonical-equal record" → Task 2 Step 1.
- "Counting invariant: filled += 1 iff attached, at most 1 per citation" → Task 2 Step 1 (canonical pass gated on `not attached`) + Step 2 (probe is the only addition inside `if attached:`; `filled += 1` unchanged).
- "New ATTACH_CANONICAL probe, reuses via=" → Task 2 Step 2.
- "_canonicalize_url unchanged" → no task touches relevance.py; confirmed consumed-only.
- "_classify_url_diff unchanged" → no task touches it; Task 2 Step 2 calls it as-is.
- Test coverage: trailing-slash (flip), Steam, exact-precedence, five-key-fields, genuine-miss-still-fires, query-anti-mismatch (pre-existing, still valid) → Task 1.
- "Full suite green" → Task 2 Step 4.
- "Probe behavior matrix" → covered by Task 1's five tests + the pre-existing query-differs test.

**Placeholder scan:** none — every code step carries literal code; every test step carries runnable code with exact imports and assertions. The `<the compose file>` / `<run-start>` / `<research-id>` tokens in Task 3 are environment values the operator supplies, not plan placeholders (they are not knowable in advance).

**Type consistency:** `canonical_hit: str | None`, `attached: bool`, `filled: int`, `via: str` used consistently. `canonical_hit` is assigned in Task 2 Step 1 and read in Step 2 within the same function scope; both steps are in the same task so a single implementer holds both. `_canonicalize_url` signature `str -> str` matches both call sites. `_classify_url_diff(a, b) -> str` matches the one new call.

**Known gap, deliberately out of scope:** the 3 genuine-noise misses from the 2026-08-12 run (`baidu.com/?a=`, the two `sh-act.org` PHP query-string URLs) remain `ATTACH_MISS`. They are upstream citation-quality issues, not attach-matching defects. Left for a separate plan.
