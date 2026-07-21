# SearXNG-Mode (Generic HTML Pipeline) Image Support — Design

**Date:** 2026-07-21
**Branch:** `i18n-zh-translation`
**Builds on:** `docs/superpowers/specs/2026-07-20-report-image-extraction-design.md` (the Firecrawl-only image subsystem, Tasks 1–10, already delivered)

## Goal

Let LDR embed real source-page images into reports **regardless of which
search engine fetched the content** — not just Firecrawl. Today only the
Firecrawl engine populates image data; SearXNG (the default) and every
other engine that fetches page bodies through the generic HTML pipeline
produce no images, so the image feature is silently inert under the
default configuration.

## Root Cause (verified)

The generic HTML download path already fetches the full raw HTML
(`HTMLDownloader._fetch_html()` returns `response.text`), but
`download()` runs it through `_extract_content()` → plain text and
**discards the raw HTML**. The data needed to extract images already
exists inside the pipeline; it is thrown away before images can be read.
No new network fetch is required — only "keep and pass through".

## Unified Gating Invariant (core constraint)

A single switch — the existing `report.enable_images` — governs **both**
fetch paths identically:

| `report.enable_images` | Firecrawl path | Generic HTML path (SearXNG, DDG, Tavily, …) |
|---|---|---|
| **ON** | scrape requests `formats:["markdown","html"]`; extract images from HTML → store image-list JSON | extract images from the already-downloaded HTML → store image-list JSON |
| **OFF (default)** | scrape requests `formats:["markdown"]` only (today's behavior) | `fetch_content` text-only; HTML never parsed for images |

**When OFF, observable downstream behavior is byte-for-byte identical to today**
for both paths (SearXNG returns text only — unchanged; Firecrawl scrape's
parsed text + `html_content` semantics unchanged because the previously-fetched
html is no longer stored on the SearchResult row). Wire-level: Firecrawl
previously requested `["markdown","html"]` unconditionally; OFF now requests
`["markdown"]` only (saves bandwidth on the Firecrawl call, no behavioral
difference to the report). This invariant is locked by tests.

## Architecture & Data Flow

Both paths converge on ONE artifact: an **image-list JSON** stored in
`SearchResult.html_content`. Post-processing consumes only that one
format.

```
run_research_process
 └─ (generic engine) engine._get_full_content
      └─ FullSearchResults._get_full_content
           └─ [enable_images ON] pipeline.fetch_content_with_images(urls, titles)
                └─ per url: html = downloader._fetch_html(url)   # already fetched
                            text = extract+format(html)          # existing text
                            imgs = extract_images(html, url, title)  # reuse Task 1
                → {url: {"text": str|None, "images": [ExtractedImage...]}}
           → item["html_content"] = dumps_images(images)         # image-list JSON
 └─ (firecrawl engine) _get_full_content
      └─ [enable_images ON] scrape(formats=[md,html]); imgs = extract_images(html)
           → item["html_content"] = dumps_images(images)         # SAME format
 └─ (post-processing) enhance_report_with_images
      └─ loads_images(sr.html_content) → ImageBank → enhance → store → rewrite
```

`report.enable_images` OFF → `fetch_content_with_images` is never called
(generic path uses plain `fetch_content`), and Firecrawl `scrape` requests
markdown only.

## Components & Interfaces

**New: `images/serialize.py`** — single serialization point, shared by both paths.
```python
def dumps_images(images: List[ExtractedImage]) -> str   # → JSON array
def loads_images(raw: str | None) -> List[ExtractedImage]  # JSON → list; non-JSON/empty/None → []
```
`loads_images` is tolerant: any legacy raw-HTML value, empty string, None,
or malformed JSON yields `[]` (never raises).

**New: `HTMLDownloader.download_with_html(url) -> tuple[Optional[bytes], Optional[str]]`**
Returns `(extracted_text_bytes, raw_html)` from a **single** fetch. Existing
`download()` is unchanged.

**New: `pipeline.fetch_content_with_images(urls, titles=None, settings_snapshot=None, language="English", enable_js_rendering=False) -> Dict[str, Dict[str, Any]]`**
Returns `{url: {"text": str|None, "images": List[ExtractedImage]}}`. Reuses
the existing `batch_fetch_and_extract` download logic; at the point the raw
`html` is in hand, also runs `extract_images(html, url, title)`. Image
extraction is wrapped in its own try/except so a parse failure never
affects text extraction. `fetch_content` is unchanged.

**Changed: `FullSearchResults._get_full_content`** — when `enable_images`
is ON, call `fetch_content_with_images` and set
`item["html_content"] = dumps_images(images)`; otherwise unchanged.

**Changed: Firecrawl engine + client** — `scrape()`'s `formats` is
decided by the gate (OFF → `["markdown"]` only). When ON, the engine runs
`extract_images` on the returned HTML and stores
`item["html_content"] = dumps_images(images)` (image-list JSON, not raw HTML).

**Changed: `enhance_report_with_images` (postprocessing.py)** — build the
`ImageBank` from `loads_images(sr.html_content)` instead of parsing HTML.

## Error Handling & Boundaries

Images are a report enhancement; **no failure may break the research run** —
degrade to a text-only report.

| Stage | Failure | Handling |
|---|---|---|
| `_fetch_html` | network/timeout/non-HTML | returns None; url has no text/images; skipped (existing behavior) |
| `extract_images` | malformed HTML / bs4 error | that url's `images=[]`; **text extraction unaffected** (decoupled try/except) |
| `dumps_images` | serialization error | store `[]`; result still persists |
| `loads_images` | legacy HTML / non-JSON / empty / None | return `[]`; empty bank → post-processing returns original markdown |
| `fetch_content_with_images` | unexpected error | degrade the batch to `fetch_content` (text-only), images empty |

**Key boundaries:**
1. **Gate OFF (default):** `fetch_content_with_images` not called; Firecrawl
   requests markdown only — byte-for-byte unchanged.
2. **Legacy data:** historical `html_content` values (raw HTML or empty) →
   `loads_images` returns `[]`; no crash, no misparse.
3. **Mixed engines in one research:** some results from Firecrawl, some from
   SearXNG — both store the same image-list JSON, so post-processing needs no
   source discrimination.
4. **Empty image list:** empty bank → existing "return original markdown" path
   (Task 5/8).

**Security:** no new network requests (images extracted from already-downloaded
HTML); no new SSRF surface. Actual image download still goes through the
existing `ImageStore` + `safe_get` (Task 6).

## Testing Strategy

TDD via host-write → `docker cp` → in-container pytest (existing image workflow).

**New unit tests:**
- `test_serialize.py` — round-trip; non-JSON → `[]`; empty → `[]`; legacy HTML string → `[]`.
- `test_fetch_content_with_images.py` — returns {text, images}; image failure doesn't affect text; gate OFF uses `fetch_content`; no extra network (assert `_fetch_html` called once per url).
- `test_download_with_html.py` — returns `(text, html)` from a single fetch.

**Regression (updated):**
- `test_postprocessing.py` — `html_content` as image-list JSON builds bank correctly; legacy HTML / non-JSON → empty bank → original markdown.
- `test_firecrawl_scrape_html.py` — gate ON requests `["markdown","html"]`; OFF requests `["markdown"]`.

**Integration (closeout):**
- Gate OFF: both paths' `fetch_content` behavior byte-identical (regression assertion).
- Mixed batch: Firecrawl + SearXNG results both build a bank via `loads_images`.
- Full image suite regression green (existing 33 + new).

## Changed / New Files

**New:** `images/serialize.py`, `tests/images/test_serialize.py`,
`tests/images/test_fetch_content_with_images.py`,
`tests/images/test_download_with_html.py`.

**Changed:** `images/__init__.py` (export serialize),
`research_library/downloaders/html.py` (`download_with_html`),
`research_library/downloaders/extraction/pipeline.py` (`fetch_content_with_images`),
`web_search_engines/engines/full_search.py` (wire new function),
`web_search_engines/engines/search_engine_firecrawl.py` (gate + image-list JSON),
`research_library/downloaders/extraction/firecrawl_client.py` (gate scrape formats),
`images/postprocessing.py` (`loads_images` input),
`tests/images/test_postprocessing.py`, `tests/images/test_firecrawl_scrape_html.py`.

## Interaction with Delivered Work (Tasks 1–10)

- **Reused unchanged:** `extract_images` (T1), `ImageBank` (T2), `VisionDescriber` (T3),
  `ImageEnhancer` (T5), `ImageStore` (T6), post-processing skeleton (T8), routes/cascade (T9).
- **Reworked:** T7 (Firecrawl scrape now gated + stores image-list JSON) and the
  input-parsing half of `enhance_report_with_images` (T8: HTML → image-list JSON).
- **`SearchResult.html_content`:** semantics change from "raw HTML" to "image-list
  JSON"; column name and migration 0011 **unchanged** (no new migration). Documented
  in code comment.

## YAGNI

No metadata passthrough beyond `titles`; no top-N limit (full coverage chosen);
no new settings; no new migration; no changes to non-Firecrawl engines that don't
go through the generic HTML pipeline.

## Confirmed Scope (no caller outside these files)

Verified against current shipped source:
- `FirecrawlClient.scrape()` callers: only `search_engine_firecrawl.py:152`.
  Task 5 may change the `include_html` default freely.
- `images.extractor.extract_images` callers: only `images/postprocessing.py:39`.
  Task 6 may remove this import from `postprocessing.py`.
