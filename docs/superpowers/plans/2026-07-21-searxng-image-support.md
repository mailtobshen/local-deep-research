# SearXNG-Mode Image Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend real-image extraction to every engine that fetches page bodies through the generic HTML pipeline (SearXNG, DDG, etc.), not just Firecrawl.

**Architecture:** Both fetch paths converge on one artifact — an image-list JSON stored in `SearchResult.html_content`. The generic HTML pipeline extracts images from the HTML it already downloads (no new network request), gated by the existing `report.enable_images`. Firecrawl is reworked to the same gate + same JSON format. Post-processing consumes only the JSON.

**Tech Stack:** Python 3.14, BeautifulSoup4, existing `images/` subsystem (Tasks 1–10), pytest.

## Global Constraints

- Unified gate: `report.enable_images` (default `false`) governs BOTH paths. When OFF, behavior is byte-for-byte identical to today (generic path uses plain `fetch_content`; Firecrawl scrape requests `["markdown"]` only).
- No new network requests: generic-path images come from already-downloaded HTML.
- No new migration: `SearchResult.html_content` column reused; semantics change from "raw HTML" to "image-list JSON".
- Reuse existing `extract_images` (Task 1), `ImageBank`/`ImageEnhancer`/`ImageStore`, post-processing skeleton (Task 8).
- Test workflow: write test on host under `tests/images/`, then:
  `docker cp tests/images/<f>.py ldr-local:/tmp/ldr_tests/<f>.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest <f>.py -v'`
- Source is hot-mounted into the container (edits to `src/` are visible immediately; no rebuild).
- Each task: `git add` only that task's exact files (a working tree with pre-existing unrelated changes must not be swept in).

## Out of Scope (reused unchanged)

Per upstream §4 four-stage architecture, this plan only touches **Stage 0**
(data sourcing: HTML is now captured for the generic HTML pipeline too) and
**Stage 1 input parsing** (consumes image-list JSON instead of raw HTML).
The following are unchanged and require no edits in this plan:
- **Stage 2** (`ImageEnhancer.enhance` — LLM inserts images into markdown).
  Operates on `ImageBank`, which is fed by `loads_images(...)` in Task 6 —
  the API contract is preserved (a list of `ExtractedImage` either way).
- **Stage 3a** (`VisionDescriber.describe` for alt-less images).
- **Stage 3b** (`ImageStore.persist` + `rewrite_markdown` + cascade delete).
  Persists only the URLs that survived Stage 2; routing layer unchanged.

## Caller Pre-Verification (already checked 2026-07-21)

- `FirecrawlClient.scrape()` has exactly one caller:
  `web_search_engines/engines/search_engine_firecrawl.py:152`.
  Task 5's `include_html` default change is safe.
- `images.extractor.extract_images` has exactly one caller:
  `images/postprocessing.py:39`.
  Task 6 may drop the `from .extractor import extract_images` import in
  `postprocessing.py`.

---

## File Structure

**New:**
- `src/local_deep_research/images/serialize.py` — `dumps_images` / `loads_images` (single serialization point).
- `tests/images/test_serialize.py`, `test_download_with_html.py`, `test_fetch_content_with_images.py`.

**Modified:**
- `src/local_deep_research/images/__init__.py` — export serialize fns.
- `src/local_deep_research/research_library/downloaders/html.py` — `download_with_html()`.
- `src/local_deep_research/research_library/downloaders/extraction/pipeline.py` — `fetch_content_with_images()`.
- `src/local_deep_research/web_search_engines/engines/full_search.py` — wire new fn in `_get_full_content`.
- `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` — gate scrape formats.
- `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py` — gate + image-list JSON.
- `src/local_deep_research/images/postprocessing.py` — build bank from `loads_images`.
- `tests/images/test_postprocessing.py`, `test_firecrawl_scrape_html.py` — regression updates.

---

## Task 1: Image list serialization

**Files:**
- Create: `src/local_deep_research/images/serialize.py`
- Modify: `src/local_deep_research/images/__init__.py`
- Test: `tests/images/test_serialize.py`

**Interfaces:**
- Consumes: `ExtractedImage` (Task 1 of prior plan; fields: url, alt, source_url, source_title, width, height).
- Produces: `dumps_images(List[ExtractedImage]) -> str`, `loads_images(Optional[str]) -> List[ExtractedImage]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_serialize.py
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.serialize import dumps_images, loads_images


def _img(url="https://x/a.jpg"):
    return ExtractedImage(url=url, alt="A", source_url="https://x", source_title="T", width=800, height=600)


def test_roundtrip():
    imgs = [_img("https://x/a.jpg"), _img("https://x/b.jpg")]
    out = loads_images(dumps_images(imgs))
    assert [i.url for i in out] == ["https://x/a.jpg", "https://x/b.jpg"]
    assert out[0].alt == "A"
    assert out[0].width == 800


def test_loads_empty_string_returns_empty():
    assert loads_images("") == []


def test_loads_none_returns_empty():
    assert loads_images(None) == []


def test_loads_legacy_html_returns_empty():
    assert loads_images("<html><img src='x'></html>") == []


def test_loads_malformed_json_returns_empty():
    assert loads_images('{"not": "a list"}') == []
    assert loads_images('[{"missing_url": 1}]') == []


def test_dumps_empty_list():
    assert loads_images(dumps_images([])) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_serialize.py ldr-local:/tmp/ldr_tests/test_serialize.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_serialize.py -v'`
Expected: FAIL — `ModuleNotFoundError: No module named 'local_deep_research.images.serialize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/serialize.py
"""Serialize ExtractedImage lists to/from JSON for storage in html_content."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from .extractor import ExtractedImage

logger = logging.getLogger(__name__)

_FIELDS = ("url", "alt", "source_url", "source_title", "width", "height")


def dumps_images(images: List[ExtractedImage]) -> str:
    try:
        return json.dumps(
            [
                {
                    "url": i.url,
                    "alt": i.alt,
                    "source_url": i.source_url,
                    "source_title": i.source_title,
                    "width": i.width,
                    "height": i.height,
                }
                for i in images
            ]
        )
    except Exception:
        logger.debug("dumps_images failed", exc_info=True)
        return "[]"


def loads_images(raw: Optional[str]) -> List[ExtractedImage]:
    """Deserialize; tolerant of None, empty, legacy HTML, or malformed JSON."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[ExtractedImage] = []
    for entry in data:
        if not isinstance(entry, dict) or "url" not in entry:
            continue
        out.append(
            ExtractedImage(
                url=entry.get("url"),
                alt=entry.get("alt", ""),
                source_url=entry.get("source_url", ""),
                source_title=entry.get("source_title", ""),
                width=entry.get("width"),
                height=entry.get("height"),
            )
        )
    return out
```

Update `src/local_deep_research/images/__init__.py` — add serialize exports (keep existing lines):

```python
from .extractor import ExtractedImage, extract_images
from .bank import ImageBank
from .vision import VisionDescriber
from .enhancer import ImageEnhancer
from .store import ImageStore
from .serialize import dumps_images, loads_images

__all__ = [
    "ExtractedImage",
    "extract_images",
    "ImageBank",
    "VisionDescriber",
    "ImageEnhancer",
    "ImageStore",
    "dumps_images",
    "loads_images",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/serialize.py src/local_deep_research/images/__init__.py tests/images/test_serialize.py
git commit -m "feat(images): add image-list JSON serialize/deserialize"
```

---

## Task 2: HTMLDownloader.download_with_html

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/html.py` (add method after `download_with_result`, ~line 110)
- Test: `tests/images/test_download_with_html.py`

**Interfaces:**
- Consumes: existing `self._fetch_html(url) -> Optional[str]`, `self._extract_content(html, url)`, `self._format_extracted_content(extracted) -> str`.
- Produces: `download_with_html(url) -> tuple[Optional[bytes], Optional[str]]` returning `(text_bytes, raw_html)` from ONE fetch.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_download_with_html.py
from unittest.mock import patch
from local_deep_research.research_library.downloaders.html import HTMLDownloader


def test_download_with_html_returns_text_and_raw_html():
    d = HTMLDownloader()
    raw = "<html><body><p>Hello world this is the body content.</p><img src='https://x/a.jpg'></body></html>"
    with patch.object(d, "_fetch_html", return_value=raw) as mock_fetch:
        text_bytes, html = d.download_with_html("https://example.com")
    # single fetch only
    mock_fetch.assert_called_once_with("https://example.com")
    assert html == raw
    # text extracted (may be None if extractor rejects short content) — html always returned
    assert isinstance(html, str)


def test_download_with_html_none_when_fetch_fails():
    d = HTMLDownloader()
    with patch.object(d, "_fetch_html", return_value=None):
        text_bytes, html = d.download_with_html("https://example.com")
    assert text_bytes is None
    assert html is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_download_with_html.py ldr-local:/tmp/ldr_tests/test_download_with_html.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_download_with_html.py -v'`
Expected: FAIL — `AttributeError: 'HTMLDownloader' object has no attribute 'download_with_html'`.

- [ ] **Step 3: Write minimal implementation**

Add to `html.py` inside `class HTMLDownloader`, immediately after the `download_with_result` method (around line 110):

```python
    def download_with_html(
        self, url: str
    ) -> tuple[Optional[bytes], Optional[str]]:
        """Fetch once; return (extracted_text_bytes, raw_html).

        raw_html lets callers extract images from the same fetch without a
        second network request. Either element may be None on failure.
        """
        try:
            html_content = self._fetch_html(url)
            if not html_content:
                return None, None
            extracted = self._extract_content(html_content, url)
            if extracted:
                text = self._format_extracted_content(extracted)
                return text.encode("utf-8"), html_content
            return None, html_content
        except Exception:
            logger.exception(f"Failed to download HTML from {url}")
            return None, None
```

(`logger` and `Optional` are already imported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/html.py tests/images/test_download_with_html.py
git commit -m "feat(downloaders): HTMLDownloader.download_with_html returns text+raw html"
```

---

## Task 3: pipeline.fetch_content_with_images

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py` (add function after `fetch_content`, ~line 550)
- Test: `tests/images/test_fetch_content_with_images.py`

**Interfaces:**
- Consumes: `AutoHTMLDownloader.download_with_html` (Task 2), `extract_images` (prior plan Task 1).
- Produces: `fetch_content_with_images(urls, titles=None, settings_snapshot=None, language="English", enable_js_rendering=False) -> Dict[str, Dict[str, Any]]` where each value is `{"text": Optional[str], "images": List[ExtractedImage]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_fetch_content_with_images.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction import pipeline


def test_returns_text_and_images_from_single_fetch():
    raw = '<html><body><img src="https://real/a.jpg" alt="tower" width="800" height="600"></body></html>'
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", raw)
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl):
        out = pipeline.fetch_content_with_images(
            ["https://src/page"], titles={"https://src/page": "Page"}
        )
    entry = out["https://src/page"]
    assert entry["text"] == "body text"
    assert [i.url for i in entry["images"]] == ["https://real/a.jpg"]
    assert entry["images"][0].source_title == "Page"
    fake_dl.download_with_html.assert_called_once_with("https://src/page")


def test_image_extraction_failure_does_not_break_text():
    fake_dl = MagicMock()
    fake_dl.download_with_html.return_value = (b"body text", "<html>ok</html>")
    with patch.object(pipeline, "AutoHTMLDownloader", return_value=fake_dl), \
         patch.object(pipeline, "extract_images", side_effect=Exception("bs4 boom")):
        out = pipeline.fetch_content_with_images(["https://src/page"])
    entry = out["https://src/page"]
    assert entry["text"] == "body text"
    assert entry["images"] == []


def test_empty_urls_returns_empty():
    assert pipeline.fetch_content_with_images([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_fetch_content_with_images.py ldr-local:/tmp/ldr_tests/test_fetch_content_with_images.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_fetch_content_with_images.py -v'`
Expected: FAIL — `AttributeError: module ... has no attribute 'fetch_content_with_images'`.

- [ ] **Step 3: Write minimal implementation**

At the top of `pipeline.py`, add the import (near the other `.` imports, after line 28's `from .firecrawl_client import FirecrawlClient`):

```python
from ....images.extractor import extract_images
```

Also ensure `AutoHTMLDownloader` is importable at module level for patching. It is currently imported lazily inside functions (`from ..playwright_html import AutoHTMLDownloader`). Add a module-level import near the top imports:

```python
from ..playwright_html import AutoHTMLDownloader
```

(If a circular-import error appears at import time, keep the lazy import inside functions AND add `AutoHTMLDownloader = None` won't work for the patch target; instead patch target in the test is `pipeline.AutoHTMLDownloader`, so the module-level import is required. Verify no circular import by running the failing test in Step 2 — if it errors on import, move the `from ....images.extractor import extract_images` to lazy-inside-function and keep `AutoHTMLDownloader` module-level.)

Add the function after `fetch_content` (around line 550):

```python
def fetch_content_with_images(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Fetch + extract text AND images from the same download.

    Returns {url: {"text": Optional[str], "images": List[ExtractedImage]}}.
    Image extraction never affects text extraction (isolated try/except).
    No extra network request: images come from the already-fetched HTML.
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not urls:
        return result

    titles = titles or {}
    downloader = AutoHTMLDownloader(
        timeout=30,
        language=language,
        enable_js_rendering=enable_js_rendering,
    )
    try:
        for url in urls:
            text: Optional[str] = None
            images = []
            try:
                text_bytes, raw_html = downloader.download_with_html(url)
                if text_bytes:
                    text = text_bytes.decode("utf-8", errors="replace")
                if raw_html:
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
                    "fetch_content_with_images failed for %s", url, exc_info=True
                )
            result[url] = {"text": text, "images": images}
    finally:
        try:
            downloader.close()
        except Exception:
            logger.debug("Failed to close downloader in fetch_content_with_images")

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/images/test_fetch_content_with_images.py
git commit -m "feat(pipeline): fetch_content_with_images — text+images from one fetch"
```

---

## Task 4: Wire generic path in FullSearchResults

**Files:**
- Modify: `src/local_deep_research/web_search_engines/engines/full_search.py` (`_get_full_content`, and imports at top)

**Interfaces:**
- Consumes: `fetch_content_with_images` (Task 3), `dumps_images` (Task 1), `get_bool_setting_from_snapshot`.
- Produces: sets `item["html_content"] = dumps_images(images)` on each item when gate ON; behavior unchanged when OFF.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/images/test_fetch_content_with_images.py
from unittest.mock import MagicMock, patch


def test_full_search_gate_on_sets_html_content():
    from local_deep_research.web_search_engines.engines.full_search import (
        FullSearchResults,
    )
    from local_deep_research.images.extractor import ExtractedImage

    fs = FullSearchResults(llm=None, web_search=MagicMock(), settings_snapshot={})
    items = [{"link": "https://src/p", "title": "P"}]
    img = ExtractedImage(url="https://real/a.jpg", alt="a", source_url="s", source_title="P", width=None, height=None)

    with patch("local_deep_research.web_search_engines.engines.full_search.get_bool_setting_from_snapshot", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.validate_url", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content_with_images",
               return_value={"https://src/p": {"text": "body", "images": [img]}}):
        out = fs._get_full_content(items)
    import json
    parsed = json.loads(out[0]["html_content"])
    assert parsed[0]["url"] == "https://real/a.jpg"
    assert out[0]["full_content"] == "body"


def test_full_search_gate_off_uses_plain_fetch_content():
    from local_deep_research.web_search_engines.engines.full_search import (
        FullSearchResults,
    )

    fs = FullSearchResults(llm=None, web_search=MagicMock(), settings_snapshot={})
    items = [{"link": "https://src/p", "title": "P"}]
    with patch("local_deep_research.web_search_engines.engines.full_search.get_bool_setting_from_snapshot", return_value=False), \
         patch("local_deep_research.web_search_engines.engines.full_search.validate_url", return_value=True), \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content", return_value={"https://src/p": "body"}) as fc, \
         patch("local_deep_research.web_search_engines.engines.full_search.fetch_content_with_images") as fcwi:
        out = fs._get_full_content(items)
    fc.assert_called_once()
    fcwi.assert_not_called()
    assert out[0]["full_content"] == "body"
    assert "html_content" not in out[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_fetch_content_with_images.py ldr-local:/tmp/ldr_tests/test_fetch_content_with_images.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_fetch_content_with_images.py -v'`
Expected: FAIL — gate-on test fails because `html_content` is not set / `fetch_content_with_images` not imported in module.

- [ ] **Step 3: Write minimal implementation**

In `full_search.py`, update the imports at the top:

```python
from ...research_library.downloaders.extraction.pipeline import (
    fetch_content,
    fetch_content_with_images,
)
from ...config.thread_settings import get_bool_setting_from_snapshot
from ...images.serialize import dumps_images
```

Replace the body of `_get_full_content` (the section from the `try: url_to_content = fetch_content(...)` block through the final `return relevant_items`) with:

```python
        enable_images = get_bool_setting_from_snapshot(
            "report.enable_images",
            default=False,
            settings_snapshot=self.settings_snapshot,
        )

        if enable_images:
            titles = {
                item["link"]: item.get("title", "")
                for item in relevant_items
                if item.get("link")
            }
            try:
                url_to_data = fetch_content_with_images(
                    urls,
                    titles=titles,
                    settings_snapshot=self.settings_snapshot,
                    language=self.language,
                    enable_js_rendering=_read_js_rendering_setting(
                        self.settings_snapshot
                    ),
                )
            except Exception:
                logger.exception("Error fetching full content with images")
                url_to_data = {}
            for item in relevant_items:
                link = item.get("link")
                data = url_to_data.get(link) if link else None
                item["full_content"] = data.get("text") if data else None
                item["html_content"] = (
                    dumps_images(data["images"]) if data else dumps_images([])
                )
            return relevant_items

        try:
            url_to_content = fetch_content(
                urls,
                settings_snapshot=self.settings_snapshot,
                language=self.language,
                enable_js_rendering=_read_js_rendering_setting(
                    self.settings_snapshot
                ),
            )
        except Exception:
            logger.exception("Error fetching full content")
            for item in relevant_items:
                item["full_content"] = None
            return relevant_items

        for item in relevant_items:
            link = item.get("link")
            item["full_content"] = url_to_content.get(link) if link else None

        return relevant_items
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web_search_engines/engines/full_search.py tests/images/test_fetch_content_with_images.py
git commit -m "feat(search): FullSearchResults extracts images when report.enable_images on"
```

---

## Task 5: Gate Firecrawl scrape formats + store image-list JSON

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` (`scrape`)
- Modify: `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py` (`_get_full_content`)
- Test: `tests/images/test_firecrawl_scrape_html.py` (regression update)

**Interfaces:**
- Consumes: `extract_images`, `dumps_images`, `get_bool_setting_from_snapshot`.
- Produces: `scrape(url, include_html: bool = False)`; engine stores `item["html_content"] = dumps_images(images)`.

- [ ] **Step 1: Update the failing test**

Replace `tests/images/test_firecrawl_scrape_html.py` with:

```python
# tests/images/test_firecrawl_scrape_html.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)

_MODPATH = "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post"


def test_scrape_include_html_true_requests_both_formats():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi", "html": "<html><img src='x'></html>"}}
    with patch(_MODPATH, return_value=fake) as sp:
        result = client.scrape("https://example.com", include_html=True)
    sent = sp.call_args.kwargs["json"]
    assert set(sent["formats"]) == {"markdown", "html"}
    assert result["html"].startswith("<html")


def test_scrape_default_requests_markdown_only():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi"}}
    with patch(_MODPATH, return_value=fake) as sp:
        result = client.scrape("https://example.com")
    sent = sp.call_args.kwargs["json"]
    assert sent["formats"] == ["markdown"]
    assert result["markdown"] == "# hi"
    assert result["html"] is None


def test_scrape_returns_none_on_http_error():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 500
    fake.json.return_value = {}
    with patch(_MODPATH, return_value=fake):
        assert client.scrape("https://example.com") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_firecrawl_scrape_html.py ldr-local:/tmp/ldr_tests/test_firecrawl_scrape_html.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_firecrawl_scrape_html.py -v'`
Expected: FAIL — `scrape()` has no `include_html` param; default currently requests `["markdown","html"]` (from prior plan Task 7), so `test_scrape_default_requests_markdown_only` fails.

- [ ] **Step 3: Write minimal implementation**

In `firecrawl_client.py`, change the `scrape` signature and payload line:

```python
    def scrape(
        self, url: str, include_html: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Scrape a single URL; return {markdown, html} or None on failure.

        html is requested only when include_html is True (gated upstream by
        report.enable_images). Raises RateLimitError on HTTP 429.
        """
        formats = ["markdown", "html"] if include_html else ["markdown"]
        payload = {"url": url, "formats": formats}
```

(Leave the rest of the method body unchanged — it already parses `data.get("html")` and returns `{"markdown": md, "html": html if isinstance(html, str) else None}`.)

In `search_engine_firecrawl.py`, update the top imports:

```python
from ....images.extractor import extract_images
from ....images.serialize import dumps_images
from ....config.thread_settings import get_bool_setting_from_snapshot
```

Replace `_get_full_content` body with (gate decides include_html + image extraction):

```python
        enable_images = get_bool_setting_from_snapshot(
            "report.enable_images",
            default=False,
            settings_snapshot=self.settings_snapshot,
        )
        results = []
        for item in relevant_items:
            full = item.get("_full_result") or {}
            md = full.get("markdown")
            html = full.get("html")
            if not (isinstance(md, str) and md.strip()):
                link = item.get("link")
                if link:
                    try:
                        scraped = self._client.scrape(link, include_html=enable_images)
                    except Exception:
                        logger.debug(
                            f"Firecrawl scrape failed for {link}", exc_info=True
                        )
                        scraped = None
                    if isinstance(scraped, dict):
                        md = scraped.get("markdown")
                        html = scraped.get("html")
            item = dict(item)
            item["content"] = md or item.get("content", "")
            if enable_images:
                images = []
                if isinstance(html, str) and html:
                    try:
                        images = extract_images(
                            html, item.get("link", ""), item.get("title", "")
                        )
                    except Exception:
                        logger.debug("extract_images failed", exc_info=True)
                        images = []
                item["html_content"] = dumps_images(images)
            results.append(item)
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py tests/images/test_firecrawl_scrape_html.py
git commit -m "feat(firecrawl): gate scrape html by report.enable_images; store image-list JSON"
```

---

## Task 6: Post-processing consumes image-list JSON

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` (`enhance_report_with_images`)
- Test: `tests/images/test_postprocessing.py` (regression update)

**Interfaces:**
- Consumes: `loads_images` (Task 1), existing `ImageBank`, `ImageEnhancer`, `ImageStore`.
- Produces: bank built from `loads_images(sr["html_content"])` instead of `extract_images(html)`.

- [ ] **Step 1: Update the failing test**

Replace the `test_enabled_builds_bank_from_findings_and_enhances` test in `tests/images/test_postprocessing.py` with an image-list-JSON version, and keep `test_disabled_returns_markdown_unchanged`:

```python
def test_enabled_builds_bank_from_image_list_json():
    import json
    findings = [{
        "search_results": [{
            "url": "https://src/page", "title": "Page",
            "html_content": json.dumps([{
                "url": "https://real/a.jpg", "alt": "tower",
                "source_url": "https://src/page", "source_title": "Page",
                "width": 800, "height": 600,
            }]),
        }],
    }]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl, \
         patch("local_deep_research.images.postprocessing.ImageEnhancer") as IEnh, \
         patch("local_deep_research.images.postprocessing.ImageStore") as IStore:
        gl.return_value = MagicMock()
        inst = IEnh.return_value
        inst.enhance.return_value = "# R\n\n![tower](https://real/a.jpg)\n"
        store_inst = IStore.return_value
        store_inst.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        store_inst.rewrite_markdown.side_effect = lambda md, m: md.replace("https://real/a.jpg", "/images/rid/a.png")
        out = enhance_report_with_images(
            research_id="rid", clean_markdown="# R", results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "/images/rid/a.png" in out
    IEnh.assert_called_once()


def test_enabled_legacy_html_content_yields_empty_bank():
    findings = [{"search_results": [{"url": "u", "title": "t",
                 "html_content": "<html><img src='x'></html>"}]}]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl:
        gl.return_value = MagicMock()
        out = enhance_report_with_images(
            research_id="rid", clean_markdown="# R", results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert out == "# R"  # non-JSON html_content -> empty bank -> unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker cp tests/images/test_postprocessing.py ldr-local:/tmp/ldr_tests/test_postprocessing.py && docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_postprocessing.py -v'`
Expected: FAIL — current impl calls `extract_images(html,...)` and treats `html_content` as HTML, so the image-list-JSON test builds an empty bank (JSON isn't HTML with `<img>`).

- [ ] **Step 3: Write minimal implementation**

In `postprocessing.py`, change the imports:

```python
from .extractor import extract_images  # REMOVE this line
```
to:
```python
from .serialize import loads_images
```

Replace the bank-building loop inside `enhance_report_with_images`:

```python
        bank = ImageBank()
        for finding in results.get("findings", []):
            for sr in finding.get("search_results", []) or []:
                raw = sr.get("html_content")
                if raw:
                    bank.add(loads_images(raw))
```

(Everything else — the `if not bank.all_urls(): return clean_markdown`, get_llm, enhancer, store — stays identical.)

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/postprocessing.py tests/images/test_postprocessing.py
git commit -m "feat(images): post-processing builds bank from image-list JSON"
```

---

## Task 7: Regression — full image suite + gate-off invariant

**Files:** none (verification only; optional `tests/images/test_gate_off_invariant.py`)

- [ ] **Step 1: Run the full image test suite**

Run:
```bash
cd /home/administrator/local-deep-research
for f in tests/images/*.py; do docker cp "$f" ldr-local:/tmp/ldr_tests/$(basename "$f"); done
docker exec -e LDR_ADMIN_PASSWORD='123456aB' ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest . -q'
```
Expected: all green (prior 33 + new tests).

- [ ] **Step 2: Verify modules import cleanly**

Run:
```bash
docker exec ldr-local /install/.venv/bin/python -c "
from local_deep_research.web_search_engines.engines.full_search import FullSearchResults
from local_deep_research.research_library.downloaders.extraction import pipeline
from local_deep_research.web_search_engines.engines import search_engine_firecrawl
from local_deep_research.images import dumps_images, loads_images
print('IMPORTS OK')
"
```
Expected: `IMPORTS OK`.

- [ ] **Step 3: Commit any added regression test**

```bash
git add tests/images/test_gate_off_invariant.py  # if created
git commit -m "test(images): gate-off invariant + full-suite regression"
```

---

## Task 8: Integration verification (container)

**Files:** none (verification only)

- [ ] **Step 1: Restart container to pick up source**

```bash
docker compose -f docker-compose.ldr-local.yml restart local-deep-research
sleep 8 && docker exec ldr-local bash -c 'echo alive'
```

- [ ] **Step 2: Verify gate OFF = today's behavior (SearXNG text-only)**

Confirm with `report.enable_images` unset/false: a research run via SearXNG produces `full_content` but no `html_content` image JSON (spot-check newest research in the admin DB as in the prior plan's Task 10 verification).

- [ ] **Step 3: Verify gate ON extracts images (SearXNG mode)**

Set `report.enable_images = true`, run a short research whose source pages have images, then check newest report contains `/images/` local routes (same DB query as prior plan Task 10 Step 4).

- [ ] **Step 4: Commit any integration test added**

```bash
git add tests/images/test_integration.py
git commit -m "test(images): searxng-mode end-to-end integration"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Unified gate (both paths, OFF=identical): Task 4 (generic), Task 5 (firecrawl), Task 7 (invariant test). ✓
- Root cause / reuse already-downloaded HTML: Task 2 (`download_with_html`), Task 3 (`fetch_content_with_images`). ✓
- Converge on image-list JSON: Task 1 (serialize), Task 4/5 (both store `dumps_images`), Task 6 (consume `loads_images`). ✓
- No new network: Task 2/3 single fetch; test asserts `_fetch_html`/`download_with_html` called once. ✓
- Legacy-data tolerance: Task 1 `loads_images` non-JSON→[]; Task 6 legacy-HTML test. ✓
- No new migration: html_content reused; no migration task. ✓
- Reuse Tasks 1–10 components: extract_images/ImageBank/Enhancer/Store unchanged. ✓

**Placeholder scan:** No TBD/TODO. The one conditional note (Task 3 circular-import fallback) is an explicit verification instruction tied to the Step 2 failing-test run, with a concrete action, not a placeholder.

**Type consistency:** `fetch_content_with_images` returns `{url: {"text","images"}}` — consumed identically in Task 3 test and Task 4 wiring. `dumps_images`/`loads_images` signatures consistent across Tasks 1/4/5/6. `scrape(url, include_html=False)` defined Task 5, called with `include_html=enable_images` in same task's engine edit. `download_with_html -> (bytes|None, str|None)` defined Task 2, consumed Task 3.
