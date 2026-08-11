# Image Pipeline Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut wasted image-fetch time, ban ASCII diagrams from reports, make semantic matching use parent headings, cap per-section adoption at top-3, resize oversized images on persist, and add captions + an ATTACH_NEAR_MATCH probe.

**Architecture:** Eight surgical changes across three subsystems (fetch filtering, report quality, image presentation). Each task is independently testable and committed. No task changes attach-match logic or drops query params (anti-mismatch red lines).

**Tech Stack:** Python 3.14, pytest 9, loguru, Pillow (PIL, already a dependency), WeasyPrint (PDF, already a dependency), uv-managed venv.

**Spec:** `docs/superpowers/specs/2026-08-12-image-fetch-optimization-design.md` (commit `f95ef79f`).

## Global Constraints

- Branch: `main` is the only active branch. Run `git rev-parse --abbrev-ref HEAD` before every commit; if it does not print `main`, STOP.
- No background git. All git operations foreground/blocking only.
- After every commit run `git log --oneline -3` and confirm the new commit is at HEAD on `main`.
- Test command (host, uv venv — the container image has no pytest and must NOT be mutated):
  `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest <paths> -q`
  If `.venv` is missing, create it once with `uv sync --group dev`.
- Surgical changes only: touch only the lines these tasks name. Do not reformat, rename, or "improve" adjacent code.
- IMG-TRACE field vocabulary is fixed: per-image events carry `img_alt`, `img_url`, `img_source_url`, `cite_num`, `ref_url`. New events reuse these names.
- Anti-mismatch red lines (from spec): no attach-match logic change (still raw `==`); no query param dropping; no eTLD+1 domain fallback matching; no C-class melt/denylist.
- Deployment: source is hot-mounted read-only into the container; source edits apply on container restart, no image rebuild needed for these changes. (Verification at the end rebuilds the image anyway for a clean validation run.)

## File Structure

| File | Responsibility | Touched by |
|---|---|---|
| `src/local_deep_research/images/relevance.py` | URL helpers (`_extract_registered_domain`, `_normalize_url`); section splitting | Task 1 (blocklist helper), Task 2 (`_canonicalize_url`, `_find_parent_heading`), Task 7 (caption? no) |
| `src/local_deep_research/web/services/research_service.py` | `_deferred_image_fill` (fetch + attach loop) | Task 1 (filter), Task 3 (probe), Task 4 (none) |
| `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` | Firecrawl HTTP client | Task 4 (timeout) |
| `src/local_deep_research/report_generator.py` | `_build_no_boilerplate_directive` prompt | Task 5 (ASCII ban) |
| `src/local_deep_research/images/semantic_matcher.py` | `_canonical_section_phrase` (section→phrase for embedding) | Task 6 (parent heading) |
| `src/local_deep_research/images/postprocessing.py` | binding + placements + `enhance_report_with_images` | Task 6 (wire parent), Task 7 (top3 cap) |
| `src/local_deep_research/images/store.py` | `persist()` (download+save), `rewrite_markdown()` (URL→route) | Task 8 (resize-on-persist), Task 9 (caption HTML) |
| `src/local_deep_research/web/static/css/styles.css` | WebUI markdown CSS | Task 9 (`.ldr-img` rules) |
| `tests/web/test_deferred_image_fill.py` | deferred-fill + ATTACH_MISS tests | Tasks 1, 3 |
| `tests/images/test_*.py` | image pipeline tests | Tasks 2, 6, 7, 8, 9 |
| `tests/report_generator/test_*.py` | report prompt tests (if exists; else inline) | Task 5 |

---

### Task 1: Structural no-image domain blocklist (fetch pre-filter)

**Files:**
- Modify: `src/local_deep_research/images/relevance.py` (add module-level constant near other URL helpers, after `_normalize_url` at line ~215)
- Modify: `src/local_deep_research/web/services/research_service.py:599-642` (filter `urls_to_fetch` before `fetch_content_with_images`)
- Test: `tests/web/test_deferred_image_fill.py`

**Interfaces:**
- Consumes: `_extract_registered_domain(url) -> str` (existing, `relevance.py:218`).
- Produces: module constant `STRUCTURAL_NO_IMAGE_DOMAINS: frozenset[str]` in `relevance.py`; a `[IMG-TRACE] STRUCTURAL_SKIP` log event from `_deferred_image_fill`.

- [ ] **Step 1: Add the blocklist constant**

In `src/local_deep_research/images/relevance.py`, immediately after the `_normalize_url` function (line ~215), add:

```python
# Structural no-image domains: these sites' HTML contains no extractable
# static <img> (posts/videos are JS-injected; document previews are
# Flash/JS-rendered). This is an inherent property of the site class,
# NOT anti-bot. C-class domains (wikipedia/ctrip/360cities etc.) that
# HAVE images but occasionally fail to fetch are deliberately excluded
# — they are handled by normal fetch + probe, never this list.
STRUCTURAL_NO_IMAGE_DOMAINS: frozenset[str] = frozenset({
    # A: social/video — JS-injected media, no static <img>
    "instagram.com", "facebook.com", "pinterest.com",
    "youtube.com", "tiktok.com", "x.com", "twitter.com",
    "weibo.com", "xiaohongshu.com",
    # B: document-preview — Flash/JS-rendered content
    "wenku.baidu.com", "docin.com", "doc88.com",
})
```

- [ ] **Step 2: Write the failing test**

Append to `tests/web/test_deferred_image_fill.py`:

```python
def test_structural_no_image_domain_skipped_from_fetch(monkeypatch):
    """A cited URL on a structural no-image domain (instagram) must be
    removed from urls_to_fetch before fetch_content_with_images runs,
    and a STRUCTURAL_SKIP event emitted. The fetch stub must NOT see it.
    """
    from local_deep_research.web.services import research_service
    fetched_urls: list[str] = []
    def _fake_fetch(urls, **kwargs):
        fetched_urls.extend(urls)
        return {u: {"text": "t", "images": []} for u in urls}
    monkeypatch.setattr(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", _fake_fetch
    )
    cited_instagram = "https://www.instagram.com/some.post"
    cited_ok = "https://www.example.org/article"
    markdown = (
        "## S\n\n"
        f"x [[1]]({cited_instagram}) y [[2]]({cited_ok})。\n\n"
        "## Sources\n\n"
        f"[1] IG\n   URL: {cited_instagram}\n"
        f"[2] Ex\n   URL: {cited_ok}\n"
    )
    results = {"findings": [], "all_links_of_system": []}
    import logging
    logs: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda r: logs.append(r.getMessage())
    # loguru interop: capture via the module's logger if needed; fall back
    # to asserting on fetch behavior (primary contract).
    research_service._deferred_image_fill(
        "res-skip", final_markdown=markdown, results=results,
        settings_snapshot={"report.enable_images": True},
    )
    assert cited_instagram not in fetched_urls, (
        "instagram URL must be filtered out before fetch"
    )
    assert cited_ok in fetched_urls, (
        "non-blocklisted URL must still be fetched"
    )
```

Note: the primary contract is "instagram URL never reaches `fetch_content_with_images`". The STRUCTURAL_SKIP log assertion is secondary; if your test infra captures loguru, also assert `"STRUCTURAL_SKIP" in " ".join(logs)` and `"instagram.com"` in that line.

- [ ] **Step 3: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py::test_structural_no_image_domain_skipped_from_fetch -q`
Expected: FAIL (instagram URL is currently fetched).

- [ ] **Step 4: Add the filter to `_deferred_image_fill`**

In `src/local_deep_research/web/services/research_service.py`, after `urls_to_fetch` is computed (line 599) and before the `if not urls_to_fetch: return 0` guard (line 616), insert:

```python
    # Filter out structural no-image domains BEFORE fetching. These
    # domains' HTML has no extractable <img> (social/video JS-injected
    # media, document previews). Skipping them saves network + the
    # ~35s Firecrawl fallback timeout. Text is unaffected: this stage
    # only consumes ``entry["images"]`` (research_service.py:656).
    from ...images.relevance import (
        STRUCTURAL_NO_IMAGE_DOMAINS, _extract_registered_domain,
    )
    structural_skipped: dict[str, list[str]] = {}
    filtered: list[str] = []
    for u in urls_to_fetch:
        dom = _extract_registered_domain(u)
        if dom in STRUCTURAL_NO_IMAGE_DOMAINS:
            structural_skipped.setdefault(dom, []).append(u)
        else:
            filtered.append(u)
    if structural_skipped:
        skipped_summary = ", ".join(
            f"{d}:{len(us)}" for d, us in structural_skipped.items()
        )
        logger.info(
            f"[IMG-TRACE] STRUCTURAL_SKIP research={research_id} "
            f"count={sum(len(us) for us in structural_skipped.values())} "
            f"domains={skipped_summary}"
        )
    urls_to_fetch = filtered
```

- [ ] **Step 5: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py::test_structural_no_image_domain_skipped_from_fetch -q`
Expected: PASS.

- [ ] **Step 6: Run full deferred-fill suite for regressions**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py -q`
Expected: all pass (existing tests + new). If a pre-existing test fails because it used an instagram URL as its fixture, STOP and report — do not change the test to fit; instead note the fixture collision.

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/relevance.py src/local_deep_research/web/services/research_service.py tests/web/test_deferred_image_fill.py
git commit -m "perf(images): skip structural no-image domains in deferred fill

instagram/facebook/pinterest/youtube/tiktok/weibo/xiaohongshu and
wenku.baidu/docin/doc88 have no extractable static <img> (JS-injected
or document-preview). Filter them before fetch_content_with_images to
save network + the ~35s Firecrawl fallback timeout. Text unaffected:
this stage only reads entry['images']. Emits STRUCTURAL_SKIP probe."
git log --oneline -3
```

---

### Task 2: `_canonicalize_url` helper (for ATTACH_NEAR_MATCH probe)

**Files:**
- Modify: `src/local_deep_research/images/relevance.py` (add `_canonicalize_url` after `_normalize_url`, ~line 215)
- Test: `tests/images/test_url_helpers.py` (create or extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_canonicalize_url(url: str) -> str` — used by Task 3 (probe only). Does NOT replace `_normalize_url` (which other paths depend on).

- [ ] **Step 1: Write the failing test**

Create `tests/images/test_url_helpers.py` (or append if exists):

```python
from local_deep_research.images.relevance import _canonicalize_url


def test_canonicalize_trailing_slash():
    assert _canonicalize_url("https://a.com/page/") == "https://a.com/page"


def test_canonicalize_strip_whitespace():
    assert _canonicalize_url("  https://a.com/x  ") == "https://a.com/x"


def test_canonicalize_http_to_https():
    assert _canonicalize_url("http://a.com/x") == "https://a.com/x"


def test_canonicalize_www_prefix():
    assert _canonicalize_url("https://www.a.com/x") == "https://a.com/x"


def test_canonicalize_lowercase_host_and_scheme():
    assert _canonicalize_url("HTTPS://Example.COM/X") == "https://example.com/X"


def test_canonicalize_drops_fragment():
    assert _canonicalize_url("https://a.com/x#sec") == "https://a.com/x"


def test_canonicalize_keeps_query_verbatim():
    # Anti-mismatch red line: query is NEVER dropped/reordered.
    assert _canonicalize_url("https://a.com/x?id=1") == "https://a.com/x?id=1"
    assert (
        _canonicalize_url("https://a.com/x?id=1")
        != _canonicalize_url("https://a.com/x?id=2")
    ), "different query values must NOT canonicalize equal"


def test_canonicalize_empty_and_garbage_fail_closed():
    assert _canonicalize_url("") == ""
    # garbage must not raise; return stripped original
    assert _canonicalize_url("not a url") == "not a url"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_url_helpers.py -q`
Expected: FAIL (`_canonicalize_url` not defined).

- [ ] **Step 3: Implement `_canonicalize_url`**

In `src/local_deep_research/images/relevance.py`, after `_normalize_url` (~line 215), add:

```python
def _canonicalize_url(url: str) -> str:
    """URL same-origin canonicalization — DIAGNOSTIC PROBE USE ONLY.

    Performs only transforms that NEVER change page content:
      strip / rstrip("/") / scheme lowercase / http→https /
      host lowercase / drop "www." / drop fragment.
    Query is preserved VERBATIM (no param dropped/reordered) so
    ``?id=1`` and ``?id=2`` never canonicalize equal.

    Returns "" for empty input; returns the stripped original on any
    parse error (fail-closed: prefer no-match over wrong-match).
    """
    if not url:
        return ""
    u = url.strip()
    try:
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(u)
        scheme = (p.scheme or "https").lower()
        if scheme == "http":
            scheme = "https"
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/") or ""
        # query preserved as-is; fragment dropped
        return urlunsplit((scheme, host, path, p.query, ""))
    except Exception:
        return u
```

- [ ] **Step 4: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_url_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/relevance.py tests/images/test_url_helpers.py
git commit -m "feat(images): add _canonicalize_url for diagnostic probe

5 content-preserving transforms (strip/slash/scheme/www/fragment);
query preserved verbatim (anti-mismatch). For ATTACH_NEAR_MATCH probe
only — does not replace _normalize_url or change any match logic."
git log --oneline -3
```

---

### Task 3: ATTACH_NEAR_MATCH probe (observe-only, no match-logic change)

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py:717-723` (after ATTACH_MISS emission)
- Test: `tests/web/test_deferred_image_fill.py`

**Interfaces:**
- Consumes: `_canonicalize_url` from Task 2; the records (`results["findings"][].search_results[]`, `results["all_links_of_system"]`) already in scope at the attach loop.
- Produces: `[IMG-TRACE] ATTACH_NEAR_MATCH research=<id> cite_num=<N> ref_url=<url> canonical_match_url=<record_url> via=<type>` event. No change to `attached` or `filled`.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_deferred_image_fill.py`. Use the `loguru_caplog` fixture (`tests/conftest.py:604`):

```python
def test_attach_near_match_emitted_on_trailing_slash(loguru_caplog):
    """A miss where the record side has the same URL with a trailing
    slash must announce the canonical near-match with via=trailing_slash.
    Does NOT change attach outcome (still a miss) — observe only.
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
                "res-near", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert filled == 0                                # still a miss
    assert "ATTACH_NEAR_MATCH" in text
    assert cited in text
    assert record_url in text
    assert "via=trailing_slash" in text


def test_no_attach_near_match_when_query_differs(loguru_caplog):
    """Different query values (?id=1 vs ?id=2) must NOT produce a
    near-match — anti-mismatch red line."""
    cited = "https://example.org/p?id=1"
    record_url = "https://example.org/p?id=2"
    markdown = (
        "## S\n\n"
        f"x [[7]]({cited})。\n\n"
        "## Sources\n\n[7] Ex\n   URL: {cited}\n"
    ).format(cited=cited)
    results = {"findings": [{"search_results": [{"link": record_url}]}],
               "all_links_of_system": []}
    fetched = {cited: {"text": "t", "images": [_extracted_image(
        url="https://img/x.jpg", alt="x", source_url=cited)]}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction."
        "pipeline.fetch_content_with_images", return_value=fetched
    ):
        with loguru_caplog.at_level(logging.INFO):
            _deferred_image_fill(
                "res-near2", final_markdown=markdown, results=results,
                settings_snapshot={"report.enable_images": True},
            )
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "ATTACH_NEAR_MATCH" not in text
```

Add `import logging` at the top of the test file if not present; `_extracted_image` and `patch` are already imported there (see existing ATTACH_MISS test at line ~630).

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py::test_attach_near_match_emitted_on_trailing_slash tests/web/test_deferred_image_fill.py::test_no_attach_near_match_when_query_differs -q`
Expected: first FAIL on `assert "ATTACH_NEAR_MATCH" in text`; second PASSES already (no event emitted).

- [ ] **Step 3: Add the probe**

In `src/local_deep_research/web/services/research_service.py`, the `ATTACH_MISS` block is at lines 713-723. Immediately AFTER the `logger.info(... ATTACH_MISS ...)` call (after line 723) and BEFORE `if attached:` (line 724), insert:

```python
        # Diagnostic probe: was there a canonical near-match in the
        # records? Observes only — does NOT set attached or change
        # filled. Gathers evidence for future URL-normalization rules.
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

Then add the `_classify_url_diff` helper near the top of the file (after the imports / module helpers, before `_deferred_image_fill`). It uses only stdlib already imported:

```python
def _classify_url_diff(a: str, b: str) -> str:
    """Classify how two URLs that canonicalize equal differ in raw form.
    Single-factor priority; 'combined' when multiple; 'other' fallback.
    For the ATTACH_NEAR_MATCH probe only.
    """
    from urllib.parse import urlsplit
    pa, pb = urlsplit(a), urlsplit(b)
    diffs: list[str] = []
    sa, sb = (pa.scheme or "").lower(), (pb.scheme or "").lower()
    if sa != sb and {sa, sb} <= {"http", "https"}:
        diffs.append("scheme")
    ha, hb = (pa.netloc or "").lower(), (pb.netloc or "").lower()
    ha_n = ha[4:] if ha.startswith("www.") else ha
    hb_n = hb[4:] if hb.startswith("www.") else hb
    if ha_n == hb_n and ha != hb:
        diffs.append("www")
    pa_q, pb_q = (pa.path or ""), (pb.path or "")
    if pa_q.rstrip("/") == pb_q.rstrip("/") and pa_q != pb_q:
        diffs.append("trailing_slash")
    if (pa.fragment or "") != (pb.fragment or "") and (
        pa._replace(fragment="") == pb._replace(fragment="")
    ):
        diffs.append("fragment")
    if not diffs:
        return "other"
    return diffs[0] if len(diffs) == 1 else "combined"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py -q`
Expected: all pass (new + existing ATTACH_MISS test).

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py tests/web/test_deferred_image_fill.py
git commit -m "feat(observability): add IMG-TRACE ATTACH_NEAR_MATCH probe

On ATTACH_MISS, scan records for a canonical-equal URL (trailing-slash/
www/scheme/fragment diff) and emit ATTACH_NEAR_MATCH with the diff type.
Observe-only: does not change attach/filled. Gathers evidence for
future URL-normalization decisions without risking cross-page mismatch."
git log --oneline -3
```

---

### Task 4: Reduce Firecrawl fallback timeout to 15s

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py:15` (`DEFAULT_TIMEOUT = 30`)
- Test: `tests/research_library/downloaders/test_firecrawl_client.py` (create or extend)

**Interfaces:**
- Consumes: none.
- Produces: `DEFAULT_TIMEOUT = 15` (used as the default `timeout` arg of the Firecrawl client constructor).

- [ ] **Step 1: Confirm current value + check P95 of historical firecrawl elapsed**

```bash
grep -n "DEFAULT_TIMEOUT" src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py
```
Confirm it reads `DEFAULT_TIMEOUT = 30`.

- [ ] **Step 2: Write the test**

Create `tests/research_library/downloaders/test_firecrawl_client.py`:

```python
from local_deep_research.research_library.downloaders.extraction import (
    firecrawl_client,
)


def test_default_timeout_is_15():
    assert firecrawl_client.DEFAULT_TIMEOUT == 15
```

- [ ] **Step 3: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/research_library/downloaders/test_firecrawl_client.py -q`
Expected: FAIL (assert 30 == 15).

- [ ] **Step 4: Change the constant**

In `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py:15`, change:

```python
DEFAULT_TIMEOUT = 30
```
to:
```python
DEFAULT_TIMEOUT = 15
```

- [ ] **Step 5: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/research_library/downloaders/test_firecrawl_client.py -q`
Expected: PASS.

- [ ] **Step 6: Run any firecrawl-related tests for regressions**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/research_library/ -q`
Expected: all pass (if a test hard-coded 30, update it to 15; if it fails for another reason, STOP and report).

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py tests/research_library/downloaders/test_firecrawl_client.py
git commit -m "perf(fetch): reduce Firecrawl fallback timeout 30s→15s

via=none fetches (Playwright failed, Firecrawl also no-response) were
waiting ~35s each (max 182s observed). 15s covers normal Firecrawl P95
while cutting doomed waits roughly in half."
git log --oneline -3
```

---

### Task 5: Ban ASCII box-drawing diagrams in OUTPUT RULES

**Files:**
- Modify: `src/local_deep_research/report_generator.py:408` (before `=== END OF OUTPUT RULES ===`)
- Test: `tests/report_generator/test_no_boilerplate_directive.py` (create or extend)

**Interfaces:**
- Consumes: none.
- Produces: a new clause in `_build_no_boilerplate_directive()`'s return string. Applied at both call sites (lines 558, 577) automatically since they call the same method.

- [ ] **Step 1: Write the test**

Create `tests/report_generator/test_no_boilerplate_directive.py`:

```python
from local_deep_research.report_generator import ReportGenerator


def test_output_rules_ban_ascii_diagrams():
    """The anti-boilerplate directive must explicitly forbid ASCII
    box-drawing diagrams (┌─┐│◄►▼ etc.), which the model emits as
    'space relationship' filler."""
    # _build_no_boilerplate_directive is a method; instantiate minimally
    # or call on an instance built with light args. If the constructor
    # is heavy, call the method via an unbound reference on a Mock-self.
    directive = ReportGenerator._build_no_boilerplate_directive.__func__(object())
    lower = directive.lower()
    assert "ascii" in lower or "box-drawing" in lower, (
        "directive must mention ASCII / box-drawing"
    )
    assert "┌" in directive or "diagram" in lower, (
        "directive must show a forbidden box-drawing example or the word diagram"
    )
```

Note: if `ReportGenerator.__init__` requires heavy args and the unbound call above fails, instead grep the source string directly:

```python
def test_output_rules_ban_ascii_diagrams():
    import local_deep_research.report_generator as rg
    src = open(rg.__file__).read()
    assert "ASCII" in src or "box-drawing" in src
    assert "┌" in src  # an actual forbidden symbol cited
```

Use whichever form compiles and runs; the second is a fallback.

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/report_generator/test_no_boilerplate_directive.py -q`
Expected: FAIL (no ASCII clause yet).

- [ ] **Step 3: Add the clause**

In `src/local_deep_research/report_generator.py`, the `_build_no_boilerplate_directive` return string ends with:

```python
            "4. The reader does not need a methodology recap or a warning "
            "about AI-generated text. Just deliver the answer.\n"
            "=== END OF OUTPUT RULES ===\n\n"
```

Insert a new clause 5 BEFORE `=== END OF OUTPUT RULES ===`:

```python
            "4. The reader does not need a methodology recap or a warning "
            "about AI-generated text. Just deliver the answer.\n"
            "5. Do NOT include ASCII art, box-drawing diagrams, "
            "character-based schematics, or any hand-drawn-style "
            "layout using symbols (┌─┐│◄►▼├└┘═║ etc.). These render "
            "poorly across viewers and waste space. Describe spatial "
            "or structural relationships in prose or a table instead "
            "— never draw them with text characters.\n"
            "=== END OF OUTPUT RULES ===\n\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/report_generator/test_no_boilerplate_directive.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/report_generator.py tests/report_generator/test_no_boilerplate_directive.py
git commit -m "fix(report): ban ASCII box-drawing diagrams in OUTPUT RULES

The model was emitting ┌─┐│◄►▼ 'space relationship' filler (e.g. the
'二、空间关系图示说明' section). Add clause 5 to the anti-boilerplate
directive forbidding character-art; require prose/tables instead."
git log --oneline -3
```

---

### Task 6: Include parent heading in section phrase (semantic matching)

**Files:**
- Modify: `src/local_deep_research/images/relevance.py` (add `_find_parent_heading` after `_split_sections`, ~line 435)
- Modify: `src/local_deep_research/images/semantic_matcher.py:203` (`_canonical_section_phrase` signature + body)
- Modify: `src/local_deep_research/images/postprocessing.py:256-264` (pass parent heading when building `section_phrases`)
- Test: `tests/images/test_section_phrase_parent.py` (create)

**Interfaces:**
- Consumes: `_split_sections(markdown) -> list[(heading, body)]` (existing, `relevance.py:397`); headings include leading `#` marks.
- Produces: `_find_parent_heading(sections, idx) -> str` (new, `relevance.py`); `_canonical_section_phrase(heading, entities, parent_heading="")` (updated signature).

- [ ] **Step 1: Write the failing test**

Create `tests/images/test_section_phrase_parent.py`:

```python
from local_deep_research.images.relevance import (
    _find_parent_heading, _split_sections,
)
from local_deep_research.images.semantic_matcher import _canonical_section_phrase


def test_find_parent_heading_child_under_parent():
    md = (
        "## 上海迪士尼乐园\n"
        "intro\n"
        "### 主题园区与核心设施\n"
        "body\n"
    )
    secs = _split_sections(md)
    # find the child section index
    child_idx = next(i for i, (h, _) in enumerate(secs)
                     if "主题园区" in h)
    parent = _find_parent_heading(secs, child_idx)
    assert "上海迪士尼乐园" in parent


def test_find_parent_heading_top_level_returns_empty():
    md = "## Top\nbody\n"
    secs = _split_sections(md)
    assert _find_parent_heading(secs, 0) == ""


def test_canonical_section_phrase_includes_parent():
    phrase = _canonical_section_phrase(
        "主题园区与核心设施",
        entities=[],
        parent_heading="上海迪士尼乐园",
    )
    assert "上海迪士尼乐园" in phrase
    assert "主题园区与核心设施" in phrase


def test_canonical_section_phrase_without_parent_unchanged():
    phrase = _canonical_section_phrase(
        "Some Section", entities=["e1"], parent_heading=""
    )
    assert "Some Section" in phrase and "e1" in phrase
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_section_phrase_parent.py -q`
Expected: FAIL (`_find_parent_heading` undefined; `_canonical_section_phrase` rejects `parent_heading` kwarg).

- [ ] **Step 3: Implement `_find_parent_heading`**

In `src/local_deep_research/images/relevance.py`, after `_split_sections` (line ~432), add:

```python
def _heading_level(heading: str) -> int:
    """Return the `#` level of a heading string (1 for '#', 2 for '##', ...).
    The heading passed in is the stripped text from _split_sections,
    which INCLUDES leading '#' marks. Returns 0 if none."""
    s = heading.lstrip()
    n = 0
    while n < len(s) and s[n] == "#":
        n += 1
    return n


def _find_parent_heading(
    sections: list[tuple[str, str]], idx: int
) -> str:
    """Return the nearest preceding heading with a SMALLER `#` level.

    Used to give entity-poor subsections ('主题园区与核心设施') the
    context of their parent section ('上海迪士尼乐园') for semantic
    matching. Returns "" if none (top-level section or idx at start).
    """
    if idx <= 0 or idx >= len(sections):
        return ""
    child_level = _heading_level(sections[idx][0])
    if child_level <= 1:
        return ""
    for j in range(idx - 1, -1, -1):
        h = sections[j][0]
        if 0 < _heading_level(h) < child_level:
            return h.lstrip("#").strip()
    return ""
```

- [ ] **Step 4: Update `_canonical_section_phrase`**

In `src/local_deep_research/images/semantic_matcher.py:203`, change:

```python
def _canonical_section_phrase(heading: str, entities: Iterable[str]) -> str:
    """Build the text the embedding model encodes for one section.

    Heading contributes section topic; the entity list contributes
    domain terms. Empty inputs return ``""`` and the caller should
    skip embedding for that section.
    """
    parts: list[str] = []
    if heading:
        parts.append(heading)
    parts.extend(entities)
    return " ".join(parts).strip()
```
to:

```python
def _canonical_section_phrase(
    heading: str,
    entities: Iterable[str],
    parent_heading: str = "",
) -> str:
    """Build the text the embedding model encodes for one section.

    Heading contributes section topic; the entity list contributes
    domain terms; ``parent_heading`` (the nearest preceding higher-
    level heading) gives entity-poor subsections like '主题园区与核心
    设施' the context of their parent ('上海迪士尼乐园'). Empty inputs
    return ``""`` and the caller should skip embedding for that section.
    """
    parts: list[str] = []
    if parent_heading:
        parts.append(parent_heading)
    if heading:
        parts.append(heading)
    parts.extend(entities)
    return " ".join(parts).strip()
```

- [ ] **Step 5: Wire parent heading into `section_phrases`**

In `src/local_deep_research/images/postprocessing.py:256-264`, the loop is:

```python
        sections = _split_sections(clean_markdown)
        entity_pool = semantic_matcher.build_report_entity_pool(clean_markdown)
        section_phrases: dict[int, str] = {}
        for sidx, entities in entity_pool.items():
            if sidx >= len(sections) or not entities:
                continue
            phrase = semantic_matcher._canonical_section_phrase(sections[sidx][0], entities)
            if phrase:
                section_phrases[sidx] = phrase
```

Change to:

```python
        sections = _split_sections(clean_markdown)
        entity_pool = semantic_matcher.build_report_entity_pool(clean_markdown)
        section_phrases: dict[int, str] = {}
        for sidx, entities in entity_pool.items():
            if sidx >= len(sections) or not entities:
                continue
            parent = _find_parent_heading(sections, sidx)
            phrase = semantic_matcher._canonical_section_phrase(
                sections[sidx][0], entities, parent_heading=parent
            )
            if phrase:
                section_phrases[sidx] = phrase
```

Add `_find_parent_heading` to the existing `from .relevance import ...` line at the top of `postprocessing.py` (it already imports `_split_sections` from there).

- [ ] **Step 6: Run tests to verify they pass + regression**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ -q`
Expected: all pass (new + existing semantic-matcher tests).

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/relevance.py src/local_deep_research/images/semantic_matcher.py src/local_deep_research/images/postprocessing.py tests/images/test_section_phrase_parent.py
git commit -m "fix(images): include parent heading in section phrase

Entity-poor subsections ('主题园区与核心设施') now embed with their
parent section ('上海迪士尼乐园') prepended, raising match scores for
relevant alts. _canonical_section_phrase gains parent_heading kwarg;
_find_parent_heading walks the section list by '#' level."
git log --oneline -3
```

---

### Task 7: Per-section top-3 adoption cap

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` (binding structure to carry score ~line 435; placements construction ~line 524-540)
- Test: `tests/images/test_per_section_top3_cap.py` (create)

**Interfaces:**
- Consumes: `score` available at BIND_ADOPTED decision point (`postprocessing.py` ~line 440, `score` is in scope from the scoring loop).
- Produces: `binding` entries now `(num, sidx, score)` tuples; placements truncated to top-3 per `sidx` by score desc; new `[IMG-TRACE] SECTION_CAP` event.

- [ ] **Step 1: Locate the binding-append + placements-build code**

```bash
sed -n '430,545p' src/local_deep_research/images/postprocessing.py | grep -nE "binding|placements|score|BIND_ADOPTED"
```
Confirm:
- `binding.setdefault(img.url, []).append((num, sidx))` exists (~line 436).
- `score` is in scope at the kept-branch (~line 440, used in CANDIDATE_SCORED_DETAIL).
- `placements = sorted(...)` builds `[(sidx, url, alt), ...]` (~line 533).

- [ ] **Step 2: Write the failing test**

Create `tests/images/test_per_section_top3_cap.py`:

```python
def test_per_section_top3_cap_keeps_highest_scores():
    """When 5 candidates bind to one section with distinct scores, only
    the top-3 by score reach placements."""
    # Build a minimal bank + binding scenario where section 0 has 5
    # bindings with scores 0.9, 0.8, 0.7, 0.6, 0.5. After the cap,
    # placements for sec 0 must be exactly the 0.9/0.8/0.7 ones.
    #
    # This test exercises the placements-construction function in
    # isolation. If that logic is inline in enhance_report_with_images
    # (not a separate function), refactor it into a helper
    # `_build_placements(binding, bank_by_url, cap=3)` first, THEN
    # write this test against the helper. See Step 3.
    from local_deep_research.images.postprocessing import _build_placements
    binding = {
        "u1": [(1, 0, 0.90)],
        "u2": [(1, 0, 0.80)],
        "u3": [(1, 0, 0.70)],
        "u4": [(1, 0, 0.60)],
        "u5": [(1, 0, 0.50)],
    }
    class _Img:
        def __init__(self, url, alt): self.url, self.alt = url, alt
    bank_by_url = {
        "u1": _Img("u1", "a1"), "u2": _Img("u2", "a2"),
        "u3": _Img("u3", "a3"), "u4": _Img("u4", "a4"),
        "u5": _Img("u5", "a5"),
    }
    placements = _build_placements(binding, bank_by_url, cap=3)
    urls_in_sec0 = [u for (sidx, u, alt) in placements if sidx == 0]
    assert set(urls_in_sec0) == {"u1", "u2", "u3"}, (
        "top-3 by score must be kept; u4/u5 dropped"
    )


def test_per_section_under_3_all_kept():
    from local_deep_research.images.postprocessing import _build_placements
    binding = {"u1": [(1, 0, 0.9)], "u2": [(1, 0, 0.8)]}
    class _Img:
        def __init__(self, url, alt): self.url, self.alt = url, alt
    bank_by_url = {"u1": _Img("u1", "a1"), "u2": _Img("u2", "a2")}
    placements = _build_placements(binding, bank_by_url, cap=3)
    assert len(placements) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_per_section_top3_cap.py -q`
Expected: FAIL (`_build_placements` not defined; `binding` tuples are 2-element not 3).

- [ ] **Step 4: Carry score in binding tuples**

In `src/local_deep_research/images/postprocessing.py`, change the binding append (~line 436):

```python
                        binding.setdefault(img.url, []).append(
                            (num, sidx)
                        )
```
to:
```python
                        binding.setdefault(img.url, []).append(
                            (num, sidx, score)
                        )
```

(`score` is in scope at this point — it was computed for CANDIDATE_SCORED_DETAIL just above.)

- [ ] **Step 5: Extract `_build_placements` helper with top-3 cap**

Add a module-level helper in `postprocessing.py` (near `_dedupe_images`):

```python
SECTION_IMAGE_CAP = 3  # max images adopted per section, by score


def _build_placements(
    binding: dict,
    bank_by_url: dict,
    cap: int = SECTION_IMAGE_CAP,
) -> list[tuple[int, str, str]]:
    """Build (sidx, url, alt) placements, capped at ``cap`` per section.

    Within each section, keeps the top-``cap`` images by binding score
    (desc). Emits a SECTION_CAP IMG-TRACE per over-cap section. Caller
    must hold the research_id logger context.
    """
    # Gather per-section candidates with scores.
    by_sec: dict[int, list[tuple[float, str]]] = {}
    for url, pairs in binding.items():
        if url not in bank_by_url:
            continue
        for _num, sidx, score in pairs:
            by_sec.setdefault(sidx, []).append((score, url))
    placements: list[tuple[int, str, str]] = []
    for sidx, cands in by_sec.items():
        cands_sorted = sorted(cands, key=lambda sc: sc[0], reverse=True)
        dropped = max(0, len(cands_sorted) - cap)
        if dropped:
            logger.info(
                f"[IMG-TRACE] SECTION_CAP sec={sidx} "
                f"candidates={len(cands_sorted)} kept={cap} dropped={dropped}"
            )
        for _score, url in cands_sorted[:cap]:
            placements.append((sidx, url, bank_by_url[url].alt))
    placements.sort(key=lambda p: (p[0], p[1]))
    return placements
```

- [ ] **Step 6: Replace the inline placements construction**

In `postprocessing.py` (~line 524-540), replace the existing `placements = sorted(...)` block:

```python
        bank_by_url = {img.url: img for img in bank.candidates_with_alt()}
        placements = sorted(
            (
                (sidx, url, bank_by_url[url].alt)
                for url, pairs in binding.items()
                if url in bank_by_url
                for _num, sidx in pairs
            ),
            key=lambda p: (p[0], p[1]),
        )
```
with:

```python
        bank_by_url = {img.url: img for img in bank.candidates_with_alt()}
        placements = _build_placements(binding, bank_by_url)
```

- [ ] **Step 7: Run tests + full image suite**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ tests/web/test_deferred_image_fill.py -q`
Expected: all pass. If a binding-related test assumed 2-element tuples, update it to 3-element (that test was relying on internal shape; the public contract is `_build_placements`).

- [ ] **Step 8: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/postprocessing.py tests/images/test_per_section_top3_cap.py
git commit -m "feat(images): cap per-section adoption at top-3 by score

binding tuples now carry score; _build_placements keeps the top-3
images per section by phrase-similarity score and emits SECTION_CAP
for over-cap sections. Stops 68-image sections; keeps the most
relevant alts."
git log --oneline -3
```

---

### Task 8: Resize oversized images on persist (>600px long side)

**Files:**
- Modify: `src/local_deep_research/images/store.py:260-340` (`persist()` — add resize before save)
- Test: `tests/images/test_persist_resize.py` (create)

**Interfaces:**
- Consumes: `_MAX_DISPLAY_PX = 600` (existing, line 40); `PIL.Image` (already lazily imported at line 72 as `PILImage`).
- Produces: oversized images saved to `/data/images/<rid>/...` at reduced size (long side 600, aspect preserved); `url_to_size` reflects the SAVED (reduced) dimensions.

- [ ] **Step 1: Read the current `persist()` body to find the save point**

```bash
sed -n '260,345p' src/local_deep_research/images/store.py
```
Identify where `data` (raw bytes) is written to disk and where size is probed. The resize must happen to the bytes BEFORE the write, and the size recorded must be the post-resize size.

- [ ] **Step 2: Write the failing test**

Create `tests/images/test_persist_resize.py`:

```python
import io
from local_deep_research.images.store import ImageStore, _MAX_DISPLAY_PX


def _png_bytes(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_oversized_image_resized_on_persist(tmp_path):
    """A 1200x800 image (long side 1200 > 600) must be persisted at
    long side 600 (i.e. 600x400), aspect preserved."""
    store = ImageStore(research_id="r1", images_dir=str(tmp_path))
    url = "https://example.com/big.png"
    routes = store.persist(
        [url],
        url_to_alt={url: "big"},
        url_to_source={url: ("https://example.com", "Ex")},
        _fetcher=lambda u: _png_bytes(1200, 800),
    )
    route = routes[url]
    local = tmp_path / route.split("/")[-1]
    from PIL import Image
    with Image.open(local) as im:
        w, h = im.size
    assert max(w, h) <= _MAX_DISPLAY_PX
    # aspect 1200:800 = 3:2 → 600x400
    assert (w, h) == (600, 400)


def test_under_cap_image_not_resized(tmp_path):
    """A 400x300 image (long side 400 <= 600) is saved as-is."""
    store = ImageStore(research_id="r2", images_dir=str(tmp_path))
    url = "https://example.com/small.png"
    routes = store.persist(
        [url], url_to_alt={url: "s"}, url_to_source={url: ("https://e.com", "E"),
        _fetcher=lambda u: _png_bytes(400, 300),
    )
    local = tmp_path / routes[url].split("/")[-1]
    from PIL import Image
    with Image.open(local) as im:
        assert im.size == (400, 300)
```

Note: the exact `persist()` signature / `_fetcher` injection mechanism must match the real `persist()`. If `persist()` fetches bytes itself (no injection seam), add a `_fetch_bytes` parameter with a default that does the real fetch, and tests pass a lambda. Inspect `persist()` first (Step 1) and adapt the test to the real seam.

- [ ] **Step 3: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_persist_resize.py -q`
Expected: FAIL (oversized image saved at native size; or `_fetcher` seam absent — if absent, add the seam as part of Step 4).

- [ ] **Step 4: Add resize before save in `persist()`**

In `src/local_deep_research/images/store.py`, inside `persist()`, where the image bytes are obtained and before they are written to disk, add a resize step. The pattern (adapt variable names to the real code):

```python
            from PIL import Image as PILImage
            from io import BytesIO
            # ... data = <bytes fetched for url> ...
            # Probe + resize if oversized:
            with PILImage.open(BytesIO(data)) as im:
                w, h = im.size
                long_side = max(w, h)
                if long_side > _MAX_DISPLAY_PX:
                    scale = _MAX_DISPLAY_PX / long_side
                    new_size = (round(w * scale), round(h * scale))
                    im_resized = im.convert("RGB").resize(
                        new_size, PILImage.LANCZOS
                    )
                    buf = BytesIO()
                    im_resized.save(buf, format="JPEG", quality=85)
                    data = buf.getvalue()
                    w, h = new_size
                    logger.info(
                        f"[IMG-TRACE] PERSIST_RESIZE research={self.research_id} "
                        f"img_url={url} from={w}x{h}... "  # adapt
                    )
                # record final size for url_to_size
                final_size = (w, h)
            # ... write `data` to disk ...
            # ... url_to_size[url] = final_size ...
```

If the existing persist already does a size probe (line 72-73), fold the resize into that same `with PILImage.open(...)` block — do NOT open the image twice.

- [ ] **Step 5: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_persist_resize.py -q`
Expected: PASS.

- [ ] **Step 6: Run full image suite + store tests**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ -q`
Expected: all pass. Note: existing RESIZE-event tests in store may need updating (the RESIZE event previously meant "would be resized but isn't"; now it IS resized). If a test asserts the old "no actual resize" behavior, update it to reflect the new contract — and call this out in the commit message.

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/store.py tests/images/test_persist_resize.py
git commit -m "fix(images): resize oversized images on persist (>600px)

The long-side cap was half-disabled (RESIZE events logged but no actual
resize; oversized images rendered at native size, relying on WebUI CSS
max-width which PDF export lacks). Now PIL-resize on persist: long side
→600px, aspect preserved, JPEG q85. url_to_size reflects saved dims so
rewrite_markdown can emit correct width/height."
git log --oneline -3
```

---

### Task 9: `<figure>` + `<figcaption>` caption (WebUI + PDF)

**Files:**
- Modify: `src/local_deep_research/images/store.py:437,450,484` (the three `return f"![{alt}]({route})"` branches in `rewrite_markdown`)
- Modify: `src/local_deep_research/web/static/css/styles.css` (add `.ldr-img` rules)
- Test: `tests/images/test_rewrite_caption.py` (create)

**Interfaces:**
- Consumes: `url_to_size` (now reflects post-resize dims from Task 8); `html.escape` (already imported, line 5).
- Produces: every persisted image in the markdown becomes `<figure class="ldr-img"><img ...><figcaption>alt</figcaption></figure>`.

- [ ] **Step 1: Write the failing test**

Create `tests/images/test_rewrite_caption.py`:

```python
import re
from local_deep_research.images.store import ImageStore


def test_rewrite_emits_figure_with_caption(tmp_path):
    store = ImageStore(research_id="r1", images_dir=str(tmp_path))
    url = "https://example.com/x.jpg"
    markdown = f"![上海酒店]({url})"
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r1/abc.jpg"},
        url_to_size={url: (600, 400)},
        url_to_source={},
    )
    assert "<figure" in out and 'class="ldr-img"' in out
    assert "<figcaption>上海酒店</figcaption>" in out
    # img must carry width/height when size known
    assert re.search(r'<img[^>]*width="600"[^>]*height="400"', out)


def test_rewrite_caption_escapes_alt(tmp_path):
    store = ImageStore(research_id="r2", images_dir=str(tmp_path))
    url = "https://example.com/y.jpg"
    markdown = f"![a <b> & \"q\"]({url})"
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r2/def.jpg"},
        url_to_size={url: (300, 200)},
        url_to_source={},
    )
    assert "<b>" not in out  # raw < escaped
    assert "&lt;b&gt;" in out
    assert "&amp;" in out
    assert "&quot;" in out or "&#x27;" in out or "&#34;" in out


def test_rewrite_no_size_omits_width_height(tmp_path):
    store = ImageStore(research_id="r3", images_dir=str(tmp_path))
    url = "https://example.com/z.jpg"
    markdown = f"![alt]({url})"
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r3/ghi.jpg"},
        url_to_size={},
        url_to_source={},
    )
    assert "<figure" in out and "<figcaption>alt</figcaption>" in out
    assert "width=" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_rewrite_caption.py -q`
Expected: FAIL (output is `![alt](route)` plain markdown, no `<figure>`).

- [ ] **Step 3: Refactor `rewrite_markdown.repl()` to emit figure HTML**

In `src/local_deep_research/images/store.py`, the `repl()` function (line 394-484) has three return points that emit `![alt](route)`. Replace the return logic so all three build a `<figure>`. The cleanest change: compute `size_attrs` and a single shared return at the end of `repl()`. Concretely, change the three branches to set local vars and fall through to one return:

```python
        def repl(m: re.Match) -> str:
            nonlocal resized, under, unknown, dropped
            alt, url = m.group(1), m.group(2)
            route = url_to_route.get(url)
            src_entry = url_to_source.get(url) or (None, None)
            img_source_url = src_entry[0] or ""
            ref_url = img_source_url
            size = sizes.get(url)
            size_str = (
                f"{size[0]}x{size[1]}" if size is not None else "unknown"
            )
            if route is None:
                dropped += 1
                logger.info(
                    f"[IMG-TRACE] REWRITE_DROP research={self.research_id} "
                    f"img_alt={(alt or '')[:200]!r} img_url={url} "
                    f"img_source_url={img_source_url} cite_num=- ref_url={ref_url} "
                    f"reason=no_local_route"
                )
                return ""
            # Determine size + emit the appropriate KEEP/RESIZE event.
            if size is None:
                unknown += 1
                logger.info(
                    f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                    f"img_alt={(alt or '')[:200]!r} img_url={url} "
                    f"img_source_url={img_source_url} cite_num=- ref_url={ref_url} "
                    f"route={route} size=unknown"
                )
                size_attrs = ""
            else:
                w, h = size
                long_side = max(w, h)
                if long_side <= _MAX_DISPLAY_PX:
                    under += 1
                    logger.info(
                        f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                        f"img_alt={(alt or '')[:200]!r} img_url={url} "
                        f"img_source_url={img_source_url} cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h}"
                    )
                else:
                    resized += 1
                    logger.info(
                        f"[IMG-TRACE] RESIZE research={self.research_id} "
                        f"img_alt={(alt or '')[:200]!r} img_url={url} "
                        f"img_source_url={img_source_url} cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h} max_px={_MAX_DISPLAY_PX}"
                    )
                    logger.info(
                        f"[IMG-TRACE] REWRITE_KEEP research={self.research_id} "
                        f"img_alt={(alt or '')[:200]!r} img_url={url} "
                        f"img_source_url={img_source_url} cite_num=- ref_url={ref_url} "
                        f"route={route} size={w}x{h} max_px={_MAX_DISPLAY_PX}"
                    )
                size_attrs = f' width="{w}" height="{h}"'
            # Unified HTML figure output (WebUI + WeasyPrint PDF).
            safe_alt = html.escape(alt, quote=True)
            return (
                f'<figure class="ldr-img">'
                f'<img src="{route}" alt="{safe_alt}"{size_attrs} loading="lazy" />'
                f'<figcaption>{safe_alt}</figcaption>'
                f'</figure>'
            )
```

Note: this folds the previously-resized-only RESIZE event logic into the size-known branch. Since Task 8 already resizes on persist, `size` here is the post-resize size; the `long_side > _MAX_DISPLAY_PX` branch should rarely fire now — keep it for the unknown-size fallback path but it is mostly informational.

- [ ] **Step 4: Add `.ldr-img` CSS for WebUI**

In `src/local_deep_research/web/static/css/styles.css`, append:

```css
/* Image figures with captions (inserted by images.store.rewrite_markdown).
   Renders in WebUI AND WeasyPrint PDF export. */
.ldr-img { margin: 1em auto; text-align: center; }
.ldr-img img { max-width: 100%; height: auto; }
.ldr-img figcaption {
    font-size: 0.85em;
    color: #666;
    margin-top: 0.3em;
    text-align: center;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/test_rewrite_caption.py -q`
Expected: PASS.

- [ ] **Step 6: Run full image suite for regressions**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ tests/web/test_deferred_image_fill.py -q`
Expected: all pass. If an existing test asserted `![alt](route)` plain-markdown output from rewrite, update it to the figure HTML (call this out in the commit).

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/store.py src/local_deep_research/web/static/css/styles.css tests/images/test_rewrite_caption.py
git commit -m "feat(images): wrap inserted images in <figure> with <figcaption>

Every persisted image becomes <figure class='ldr-img'><img ...><figcaption>
alt</figcaption></figure>. figcaption at font-size 0.85em serves as the
image caption in both WebUI and WeasyPrint PDF export. img carries
width/height (post-resize) for stable layout."
git log --oneline -3
```

---

## Self-Review

**1. Spec coverage:** Each of the 8 spec items maps to a task:
- Spec 改动1 (blocklist) → Task 1 ✓
- Spec 改动2 (Firecrawl timeout) → Task 4 ✓
- Spec 改动3 (ATTACH_NEAR_MATCH probe) → Tasks 2+3 ✓ (helper + probe)
- Spec 改动4 (ASCII ban) → Task 5 ✓
- Spec 改动5 (parent heading) → Task 6 ✓
- Spec 改动6 (top-3 cap) → Task 7 ✓
- Spec 改动7 (resize on persist) → Task 8 ✓
- Spec 改动8 (caption) → Task 9 ✓

**2. Placeholder scan:** No "TBD"/"TODO". Two tasks (8, 9) note that exact variable names must be confirmed by reading the current `persist()`/`repl()` body first — this is unavoidable because those functions are long and the plan cannot hard-code line-internal variable names without risking drift. The steps instruct the engineer to read first, then edit by pattern; the test anchors the contract regardless of internal names.

**3. Type consistency:**
- `binding` tuples: Task 7 changes them from `(num, sidx)` to `(num, sidx, score)` and updates `_build_placements` to consume 3-tuples. The old inline `placements = sorted(...)` (which consumed 2-tuples) is replaced by `_build_placements`, so no stale 2-tuple consumer remains. ✓
- `_canonical_section_phrase`: Task 6 adds `parent_heading=""` kwarg (default keeps backward compat for any other caller). ✓
- `_canonicalize_url` (Task 2) consumed by Task 3's probe. ✓
- `SECTION_IMAGE_CAP` constant introduced in Task 7; `_build_placements` default uses it. ✓

**Cross-task dependency note:** Tasks 8 and 9 both touch `store.py` but at different functions (`persist` vs `rewrite_markdown`); Task 9's `size_attrs` depends on Task 8's resized `url_to_size`. Implement Task 8 before Task 9. Other tasks are independent and can be parallelized across subagents.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-image-pipeline-optimization.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
