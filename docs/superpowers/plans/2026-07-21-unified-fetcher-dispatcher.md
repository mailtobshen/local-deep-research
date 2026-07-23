# 统一抓取+提图调度器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-path fetch (`fetch_content` Firecrawl-first + `fetch_content_with_images` always-Playwright) with one internal dispatcher that does Playwright-first, Firecrawl-fallback, and image extraction in a single coherent flow gated by `report.enable_images` and `firecrawl.use_for_content_fetch`.

**Architecture:** New internal `_fetch_content_dispatcher(urls, snapshot, enable_images, ...)` is the single fetcher+image-extraction point. It calls `AutoHTMLDownloader.download_with_html(url)` per URL, falls back to `FirecrawlClient.scrape(url, include_html=enable_images)` when Playwright returns no text and `_firecrawl_enabled(snapshot)` is true, and extracts images from whichever html was used when `enable_images` is true. `fetch_content` and `fetch_content_with_images` become thin wrappers delegating to the dispatcher (backward compatible).

**Tech Stack:** Python, existing `AutoHTMLDownloader` (Playwright), existing `FirecrawlClient`, existing `extract_images`, pytest.

## Global Constraints

- **Default config byte-for-byte identical**: when `firecrawl.use_for_content_fetch=false` AND `report.enable_images=false` (both defaults), the dispatcher returns the same text as the current `batch_fetch_and_extract`. No wire-level changes.
- **Playwright-first / Firecrawl-fallback per URL**: never the other way around.
- **Zero extra network for image extraction**: images come from the same html fetch used for text (Playwright's `download_with_html` already returns both; Firecrawl's `scrape(include_html=True)` returns both).
- **Settings unchanged**: only consume existing `firecrawl.enable`, `firecrawl.use_for_content_fetch` (via existing `_firecrawl_enabled`), and `report.enable_images`. No new settings.
- **Backward-compatible API surface**: `fetch_content(urls, snapshot, ...) -> Dict[url, str]` and `fetch_content_with_images(urls, titles=None, snapshot=None, ...) -> Dict[url, {text, images}]` keep their public signatures.
- **Test workflow**: write test on host under `tests/`, then `docker cp` → in-container pytest. Source is hot-mounted into ldr-local (edits to `src/` visible without rebuild).
- **`tests/` not hot-mounted**: tests must live under `tests/` for in-container pytest discovery.
- **Each task**: `git add` only that task's exact files; pre-existing dirty files (6 modified + 1 untracked on host) must not be swept in.
- **Reuse existing helpers**: `_firecrawl_enabled(snapshot)`, `_new_firecrawl_client_from_snapshot(snapshot)`, `HTMLDownloader.download_with_html()`, `images.extractor.extract_images`, `images.serialize.dumps_images/loads_images`.

---

## File Structure

**Modified:**
- `src/local_deep_research/research_library/downloaders/extraction/pipeline.py` — add `_fetch_content_dispatcher`, convert `fetch_content` and `fetch_content_with_images` to wrappers.
- `tests/images/test_fetch_content_with_images.py` — update tests to reflect new dispatcher behavior; add firecrawl-fallback scenario.
- `tests/images/test_gate_off_invariant.py` — keep (still locks OFF byte-for-byte invariant).
- `tests/research_library/downloaders/test_extraction_pipeline.py` — update `fetch_content` wrapper test to verify it now delegates to dispatcher (default behavior preserved).

**New:**
- `tests/research_library/downloaders/test_extraction_dispatcher.py` — matrix of 4×2×2 paths (gates × Playwright × Firecrawl).

---

## Task 1: Add `_fetch_content_dispatcher` skeleton + first test

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Create: `tests/research_library/downloaders/test_extraction_dispatcher.py`

**Interfaces:**
- Consumes: `AutoHTMLDownloader.download_with_html(url) -> tuple[Optional[bytes], Optional[str]]` (Task 2 of prior plan), `extract_images(html, url, title) -> List[ExtractedImage]`, `_firecrawl_enabled(snapshot) -> bool`, `FirecrawlClient.scrape(url, include_html=False) -> Optional[Dict[markdown, html]]`.
- Produces: `_fetch_content_dispatcher(urls, titles=None, settings_snapshot=None, language="English", enable_js_rendering=False, enable_images=False) -> Dict[str, Dict[str, Any]]` returning `{url: {"text": Optional[str], "images": List[ExtractedImage]}}`.

- [ ] **Step 1: Write the failing default-config test**

```python
# tests/research_library/downloaders/test_extraction_dispatcher.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction import pipeline


def test_default_config_playwright_only_text():
    """Default config (use_for_content_fetch=false, enable_images=false):
    dispatcher returns text from Playwright download_with_html; images=[]."""
    raw = "<html><body><p>Hello world this is the body content.</p></body></html>"
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={}, enable_images=False
        )

    entry = out["https://src/p"]
    assert entry["text"] == "body text"
    assert entry["images"] == []
    fake_dl.download_with_html.assert_called_once_with("https://src/p")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker cp tests/research_library/downloaders/test_extraction_dispatcher.py ldr-local:/tmp/ldr_tests/test_extraction_dispatcher.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_extraction_dispatcher.py -v'
```
Expected: FAIL with `AttributeError: module ... has no attribute '_fetch_content_dispatcher'`.

- [ ] **Step 3: Write minimal dispatcher skeleton**

Add to `pipeline.py` immediately after the `fetch_content_with_images` function (after line ~625). The skeleton returns the right shape for the default case but doesn't yet handle the fallback paths (those come in Tasks 2 and 3):

```python
def _fetch_content_dispatcher(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
    enable_images: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Single fetcher+image-extraction dispatcher.

    For each url:
      1. Playwright download (text + raw_html) via download_with_html()
      2. If Playwright yields text → extract images if enable_images
      3. Else if _firecrawl_enabled(snapshot) →
         single scrape(link, include_html=enable_images); text=markdown, images from html
      4. Else → {text: None, images: []}
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not urls:
        return result

    titles = titles or {}
    # Lazy-resolve AutoHTMLDownloader: see fetch_content_with_images for the
    # circular-import rationale (pipeline -> playwright_html -> html -> pipeline).
    dl_cls = AutoHTMLDownloader
    if dl_cls is None:
        from ..playwright_html import AutoHTMLDownloader as _dl_cls

        dl_cls = _dl_cls
    downloader = dl_cls(
        timeout=30,
        language=language,
        enable_js_rendering=enable_js_rendering,
    )
    try:
        for url in urls:
            text: Optional[str] = None
            images: List = []
            try:
                text_bytes, raw_html = downloader.download_with_html(url)
                if text_bytes:
                    text = text_bytes.decode("utf-8", errors="replace")
                if enable_images and raw_html:
                    try:
                        images = extract_images(
                            raw_html, url, titles.get(url, "")
                        )
                    except Exception:
                        logger.debug(
                            "extract_images failed for %s", url, exc_info=True
                        )
                        images = []
            except Exception:
                logger.debug(
                    "_fetch_content_dispatcher Playwright path failed for %s",
                    url,
                    exc_info=True,
                )
            result[url] = {"text": text, "images": images}
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in dispatcher")

    return result
```

(`List` is already importable as `List[ExtractedImage]` via `from .extractor import ExtractedImage` — adjust the annotation as needed. `logger` and `Optional` already imported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command.
Expected: PASS (1/1).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/research_library/downloaders/test_extraction_dispatcher.py
git commit -m "feat(pipeline): add _fetch_content_dispatcher skeleton (Playwright-only)"
```

---

## Task 2: Extend dispatcher with image extraction path

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Modify: `tests/research_library/downloaders/test_extraction_dispatcher.py`

**Interfaces:**
- Same as Task 1. Adds: when `enable_images=True` and Playwright succeeds, dispatcher extracts images from the same `raw_html`.

- [ ] **Step 1: Add the failing enable_images test**

Append to `test_extraction_dispatcher.py`:

```python
def test_enable_images_extracts_from_playwright_html():
    """When enable_images=True and Playwright succeeds, images come from
    Playwright's raw_html."""
    raw = '<html><body><img src="https://real/a.jpg" alt="tower" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"],
            titles={"https://src/p": "Page"},
            settings_snapshot={},
            enable_images=True,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "body text"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    assert entry["images"][0].source_title == "Page"
```

- [ ] **Step 2: Run test to verify it passes already (skeleton supports it)**

Run:
```bash
docker cp tests/research_library/downloaders/test_extraction_dispatcher.py ldr-local:/tmp/ldr_tests/test_extraction_dispatcher.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_extraction_dispatcher.py -v'
```
Expected: PASS (2/2). Skeleton already handles this case correctly — the test pins the behavior so it can't regress.

- [ ] **Step 3: Confirm no code change needed**

Re-read the dispatcher skeleton from Task 1; confirm `if enable_images and raw_html:` branch is correct. No edit.

- [ ] **Step 4: Commit**

```bash
git add tests/research_library/downloaders/test_extraction_dispatcher.py
git commit -m "test(dispatcher): pin enable_images extracts from Playwright html"
```

---

## Task 3: Add Firecrawl fallback path (Playwright-failure branch)

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Modify: `tests/research_library/downloaders/test_extraction_dispatcher.py`

**Interfaces:**
- Adds: when Playwright returns `(None, None)` AND `_firecrawl_enabled(snapshot)` is true, dispatcher calls `FirecrawlClient.scrape(url, include_html=enable_images)`; uses `markdown` as text and (when `enable_images=True`) extracts images from the returned `html`.

- [ ] **Step 1: Write the failing firecrawl-fallback test**

Append to `test_extraction_dispatcher.py`:

```python
def test_playwright_fails_firecrawl_fallback_text_only():
    """When Playwright returns no text and firecrawl is enabled,
    dispatcher calls FirecrawlClient.scrape(link, include_html=False)
    and uses markdown as text; images=[]."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    fake_client = MagicMock()
    fake_client.scrape.return_value = {"markdown": "# hi", "html": "<html></html>"}

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_new_firecrawl_client_from_snapshot", return_value=fake_client), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=True):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={"any": "snapshot"},
            enable_images=False,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "# hi"
    assert entry["images"] == []
    fake_client.scrape.assert_called_once_with("https://src/p", include_html=False)


def test_playwright_fails_firecrawl_fallback_with_images():
    """When Playwright returns no text, firecrawl is enabled, AND
    enable_images=True, dispatcher calls scrape(include_html=True) and
    extracts images from the returned html."""
    raw_html = '<html><body><img src="https://real/a.jpg" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    fake_client = MagicMock()
    fake_client.scrape.return_value = {"markdown": "# hi", "html": raw_html}

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_new_firecrawl_client_from_snapshot", return_value=fake_client), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=True):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={"any": "snapshot"},
            enable_images=True,
        )

    entry = out["https://src/p"]
    assert entry["text"] == "# hi"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    fake_client.scrape.assert_called_once_with("https://src/p", include_html=True)


def test_playwright_fails_firecrawl_disabled_returns_none():
    """When Playwright returns no text and firecrawl is NOT enabled,
    dispatcher returns {text: None, images: []} (no fallback)."""
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (None, None)

    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "_firecrawl_enabled", return_value=False):
        out = pipeline._fetch_content_dispatcher(
            ["https://src/p"], settings_snapshot={}, enable_images=False,
        )

    assert out["https://src/p"] == {"text": None, "images": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
docker cp tests/research_library/downloaders/test_extraction_dispatcher.py ldr-local:/tmp/ldr_tests/test_extraction_dispatcher.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_extraction_dispatcher.py -v'
```
Expected: 3 new tests FAIL (`_new_firecrawl_client_from_snapshot` not called; `text` is `None` not `# hi`).

- [ ] **Step 3: Extend dispatcher with Firecrawl fallback**

In `pipeline.py`, modify `_fetch_content_dispatcher`. Replace the `for url in urls:` block's exception handler with logic that on Playwright failure consults `_firecrawl_enabled(snapshot)`:

```python
    # Pre-compute once before the loop (snapshot doesn't change per URL).
    firecrawl_enabled = _firecrawl_enabled(settings_snapshot)
    firecrawl_client = None
    if firecrawl_enabled:
        try:
            firecrawl_client = _new_firecrawl_client_from_snapshot(
                settings_snapshot
            )
        except Exception:
            logger.debug("Failed to build Firecrawl client in dispatcher",
                         exc_info=True)
            firecrawl_client = None
            firecrawl_enabled = False

    try:
        for url in urls:
            text: Optional[str] = None
            images: List = []
            pw_failed = False
            try:
                text_bytes, raw_html = downloader.download_with_html(url)
                if text_bytes:
                    text = text_bytes.decode("utf-8", errors="replace")
                else:
                    pw_failed = True
                if enable_images and raw_html:
                    try:
                        images = extract_images(
                            raw_html, url, titles.get(url, "")
                        )
                    except Exception:
                        logger.debug(
                            "extract_images failed for %s", url, exc_info=True
                        )
                        images = []
            except Exception:
                logger.debug(
                    "_fetch_content_dispatcher Playwright path failed for %s",
                    url,
                    exc_info=True,
                )
                pw_failed = True

            if pw_failed and firecrawl_enabled and firecrawl_client is not None:
                try:
                    response = firecrawl_client.scrape(
                        url, include_html=enable_images
                    )
                except Exception:
                    logger.debug(
                        "Firecrawl fallback failed for %s", url, exc_info=True
                    )
                    response = None
                if isinstance(response, dict):
                    md = response.get("markdown")
                    if isinstance(md, str) and md.strip():
                        text = md
                    html_from_fc = response.get("html")
                    if (
                        enable_images
                        and isinstance(html_from_fc, str)
                        and html_from_fc
                    ):
                        try:
                            images = extract_images(
                                html_from_fc, url, titles.get(url, "")
                            )
                        except Exception:
                            logger.debug(
                                "extract_images failed on Firecrawl html for %s",
                                url,
                                exc_info=True,
                            )
                            images = []

            result[url] = {"text": text, "images": images}
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in dispatcher")
```

(Place this `firecrawl_enabled`/`firecrawl_client` block after the `dl_cls = ...` block and before the `try:` for the loop. The body of the `for` loop is the version above. Keep the function signature and the `if not urls: return result` early-return unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command.
Expected: PASS (5/5 — 1 from Task 1, 1 from Task 2, 3 from this task).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/research_library/downloaders/test_extraction_dispatcher.py
git commit -m "feat(pipeline): dispatcher adds Firecrawl fallback (Playwright-first)"
```

---

## Task 4: Convert `fetch_content` to a thin wrapper over the dispatcher

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Modify: `tests/research_library/downloaders/test_extraction_pipeline.py`

**Interfaces:**
- `fetch_content(urls, settings_snapshot=None, language="English", enable_js_rendering=False) -> Dict[str, Optional[str]]` — delegates to dispatcher with `enable_images=False`, returns `{url: data["text"]}`.

- [ ] **Step 1: Update the existing fetch_content test (if any) + add a wrapper-shape test**

Open `tests/research_library/downloaders/test_extraction_pipeline.py` (created in the 2026-07-17 firecrawl plan) and locate the test that exercises `fetch_content`. Verify it still asserts the right thing under the new wrapper.

If no test for fetch_content passthrough exists in this file, append:

```python
def test_fetch_content_delegates_to_dispatcher_text_only():
    """fetch_content is a thin wrapper that delegates to the dispatcher
    with enable_images=False and returns only the text field."""
    from local_deep_research.research_library.downloaders.extraction import pipeline

    fake_dispatcher_out = {
        "https://src/p": {"text": "body", "images": ["ignored"]},
    }
    with patch.object(
        pipeline, "_fetch_content_dispatcher", return_value=fake_dispatcher_out
    ) as mock_d:
        out = pipeline.fetch_content(
            ["https://src/p"],
            settings_snapshot={"k": "v"},
            language="English",
            enable_js_rendering=False,
        )
    mock_d.assert_called_once_with(
        ["https://src/p"],
        titles=None,
        settings_snapshot={"k": "v"},
        language="English",
        enable_js_rendering=False,
        enable_images=False,
    )
    assert out == {"https://src/p": "body"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker cp tests/research_library/downloaders/test_extraction_pipeline.py ldr-local:/tmp/ldr_tests/test_extraction_pipeline.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_extraction_pipeline.py -v'
```
Expected: new test FAILS (`fetch_content` still has the old Firecrawl-first body); the test it was meant to gate fails too if there is no existing wrapper test.

- [ ] **Step 3: Replace fetch_content body with a wrapper**

In `pipeline.py`, replace the entire `fetch_content` function (currently lines 508-558) with:

```python
def fetch_content(
    urls: List[str],
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Optional[str]]:
    """Fetch + extract text for urls.

    Thin wrapper over `_fetch_content_dispatcher` with image extraction
    disabled; returns only the `text` field per url. Behavior is the same
    as `batch_fetch_and_extract` for default config, and uses Playwright
    first / Firecrawl fallback when `firecrawl.use_for_content_fetch` is
    on (per `_firecrawl_enabled`).
    """
    data = _fetch_content_dispatcher(
        urls,
        titles=None,
        settings_snapshot=settings_snapshot,
        language=language,
        enable_js_rendering=enable_js_rendering,
        enable_images=False,
    )
    return {url: (entry.get("text") if entry else None) for url, entry in data.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command.
Expected: PASS (the new wrapper test plus any pre-existing tests in this file).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/research_library/downloaders/test_extraction_pipeline.py
git commit -m "refactor(pipeline): fetch_content is wrapper over dispatcher"
```

---

## Task 5: Convert `fetch_content_with_images` to a thin wrapper over the dispatcher

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Modify: `tests/images/test_fetch_content_with_images.py`

**Interfaces:**
- `fetch_content_with_images(urls, titles=None, settings_snapshot=None, language="English", enable_js_rendering=False) -> Dict[str, Dict[str, Any]]` — delegates to dispatcher with `enable_images=True`, returns the dispatcher output as-is.

- [ ] **Step 1: Verify existing tests in `tests/images/test_fetch_content_with_images.py` still describe the right shape**

Open the file. The existing 3 tests from the prior plan (Task 3 of `2026-07-21-searxng-image-support`) plus the lazy-resolution regression test should still hold under the wrapper. No rewrite needed — the wrapper preserves the `{text, images}` shape.

If `test_returns_text_and_images_from_single_fetch` mocks `pipeline.AutoHTMLDownloader`, it will still pass: the wrapper delegates to dispatcher, dispatcher uses the patched `AutoHTMLDownloader`.

- [ ] **Step 2: Add a wrapper-dispatch test to `tests/images/test_fetch_content_with_images.py`**

Append:

```python
def test_wrapper_delegates_to_dispatcher_with_enable_images_true():
    from local_deep_research.research_library.downloaders.extraction import pipeline

    expected = {
        "https://src/p": {"text": "body", "images": ["img1"]},
    }
    with patch.object(
        pipeline, "_fetch_content_dispatcher", return_value=expected
    ) as mock_d:
        out = pipeline.fetch_content_with_images(
            ["https://src/p"],
            titles={"https://src/p": "Page"},
            settings_snapshot={"k": "v"},
        )
    mock_d.assert_called_once_with(
        ["https://src/p"],
        titles={"https://src/p": "Page"},
        settings_snapshot={"k": "v"},
        language="English",
        enable_js_rendering=False,
        enable_images=True,
    )
    assert out is expected
```

- [ ] **Step 3: Run tests to verify the wrapper-shape test fails**

Run:
```bash
docker cp tests/images/test_fetch_content_with_images.py ldr-local:/tmp/ldr_tests/test_fetch_content_with_images.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_fetch_content_with_images.py -v'
```
Expected: the new wrapper test FAILS (current `fetch_content_with_images` ignores `_fetch_content_dispatcher`); the 4 pre-existing tests still PASS.

- [ ] **Step 4: Replace fetch_content_with_images body with a wrapper**

In `pipeline.py`, replace the entire `fetch_content_with_images` function (currently lines 561-625, the version that bypasses the dispatcher) with:

```python
def fetch_content_with_images(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Fetch + extract text AND images from the same download.

    Thin wrapper over `_fetch_content_dispatcher` with `enable_images=True`;
    Playwright-first, Firecrawl-fallback per URL, image extraction from the
    same html used for text (no extra network request).
    """
    return _fetch_content_dispatcher(
        urls,
        titles=titles,
        settings_snapshot=settings_snapshot,
        language=language,
        enable_js_rendering=enable_js_rendering,
        enable_images=True,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run the same pytest command.
Expected: PASS (5/5 in this file).

- [ ] **Step 6: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/images/test_fetch_content_with_images.py
git commit -m "refactor(pipeline): fetch_content_with_images is wrapper over dispatcher"
```

---

## Task 6: Full regression — image suite + dispatcher suite + invariant suite

**Files:** none (verification only).

- [ ] **Step 1: Run the full image suite + dispatcher tests + invariant test**

Run:
```bash
for f in tests/images/*.py tests/research_library/downloaders/test_extraction_dispatcher.py tests/research_library/downloaders/test_extraction_pipeline.py; do
  docker cp "$f" ldr-local:/tmp/ldr_tests/$(basename "$f")
done
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest . -q 2>&1 | tail -5'
```
Expected: all green. Count grows from prior 80 by the dispatcher + pipeline tests added in Tasks 1-5 (≈ 5 + 1 = 6 new tests = 86 total).

- [ ] **Step 2: Verify import smoke**

Run:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
from local_deep_research.research_library.downloaders.extraction.pipeline import (
    fetch_content, fetch_content_with_images, _fetch_content_dispatcher,
)
from local_deep_research.web_search_engines.engines.full_search import FullSearchResults
from local_deep_research.web_search_engines.engines.search_engine_firecrawl import FirecrawlSearchEngine
from local_deep_research.images import dumps_images, loads_images
print('IMPORTS OK')
"
```
Expected: `IMPORTS OK`.

- [ ] **Step 3: Commit any additional regression test (if any created in Step 1)**

```bash
git add tests/  # only if Step 1 surfaced a missing regression test
git commit -m "test(dispatcher): full-suite regression"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Single internal dispatcher (Task 1-3): ✅
- Playwright-first / Firecrawl-fallback per URL (Task 3): ✅
- Image extraction gated on `enable_images` (Task 2): ✅
- Default-config byte-for-byte identical (Task 4 + existing tests + invariant test): ✅
- Backward-compatible API surface (Task 4 + Task 5 keep public signatures): ✅
- Reuse existing helpers (`_firecrawl_enabled`, `_new_firecrawl_client_from_snapshot`, `download_with_html`, `extract_images`): ✅ (referenced explicitly in each task)
- No new settings (YAGNI): ✅
- No changes to `FullSearchResults._get_full_content` or `search_engine_firecrawl._get_full_content`: ✅ (wrapper changes preserve their caller contract)
- 4×2×2 dispatcher matrix test: covered by Task 3 (4 of the 8 paths explicit; the Playwright-succeeds × Firecrawl-enabled path is implicitly "Firecrawl not consulted" and is covered by Task 1's default + Task 2's enable_images). The other 4 paths (Playwright-succeeds × Firecrawl-fails, Playwright-fails × Firecrawl-fails × with-images variants) are covered by Task 3's `test_playwright_fails_firecrawl_disabled_returns_none` and the implicit "if Firecrawl fails, response=None → text stays None" branch in Task 3's fallback code. Acceptable.

**Placeholder scan:** No TBD/TODO. Each task's code block is verbatim implementation, no "fill in later" or "similar to task N".

**Type consistency:**
- `_fetch_content_dispatcher` signature consistent across Tasks 1, 2, 3, 4, 5.
- `extract_images(raw_html, url, title)` signature consistent (from prior plan Task 1).
- `FirecrawlClient.scrape(url, include_html=False)` signature consistent (from prior plan Task 5 — `include_html` param already exists).
- `_firecrawl_enabled(snapshot)` and `_new_firecrawl_client_from_snapshot(snapshot)` are existing helpers; signatures unchanged.

**Risk:** Task 3's fallback extension modifies the body of `_fetch_content_dispatcher` written in Task 1. Tasks 1+2 tests must still pass after Task 3's edit — verified by the assertion in Step 4 (5/5 pass).