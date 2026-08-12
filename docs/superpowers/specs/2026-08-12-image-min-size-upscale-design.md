# Upscale Undersized Images on Persist (min-size floor)

**Date:** 2026-08-12
**Status:** Approved approach, pending implementation
**Scope:** Single-helper change in `images/store.py` (`_probe_and_resize`). Symmetric counterpart to the existing `_MAX_DISPLAY_PX` downscale (the image-pipeline-optimization plan's Task 8). Independent of canonical-attach and the research-summary i18n fix.

## Problem

The persistence layer only ever **downscales** oversized images (long side > `_MAX_DISPLAY_PX = 600`). Undersized images are saved at native pixel size, so a small source image (e.g. a thumbnail `Inside Jing'an Temple` at ~150×120) renders tiny in both WebUI and WeasyPrint PDF export — the `<img width= height=>` attributes reflect the tiny native size, and PDF has no CSS scaling to rescue it.

Surfaced during review of run `dfa00057`: small extracted images persisted as-is render unreadably small in the report body.

## Goal

Undersized images are upscaled at persist time so both dimensions reach a readable floor, preserving aspect ratio. The saved file (and thus `url_to_size`, the `<img>` render size, and PDF output) reflects the upscaled dimensions.

## Decisions (locked via Q&A)

- **Trigger:** pixel-area test, `w * h < _MIN_DISPLAY_AREA` (not a per-side test). Area threshold = **40,000 px²** (200²). Rationale: catches genuinely small images (thumbnails, icons) without touching modestly small but still-readable ones (e.g. 250×200 = 50,000 is left alone; 150×200 = 30,000 is upscaled).
- **Target floor:** scale so that **both** width and height are ≥ **300 px**, aspect preserved. Scale factor = `max(300/w, 300/h)`; the smaller dimension hits exactly 300, the larger exceeds it.
- **Layer:** persist-time PIL upscale inside `_probe_and_resize` (NOT a render-layer `<img width>` override). Symmetric with the existing downscale: the saved bytes are the upscaled pixels, `url_to_size` reflects them, and WebUI + PDF render identically with no renderer upscaling. Trade-off accepted: upscaling is lossy/blurry on the saved file, but a tiny image is already low-information, and consistent WebUI/PDF output outweighs preserving the original pixels.

## Non-goals

- Do NOT change the downscale path (`long_side > _MAX_DISPLAY_PX`). Only add an upscale branch.
- Do NOT touch the render layer (`rewrite_markdown`, `<img>` attrs). It already reads `url_to_size`, which will reflect the upscaled size once persist writes it.
- Do NOT change `_MAX_DISPLAY_PX`. Add a new `_MIN_DISPLAY_AREA` constant; the two are independent.
- Do NOT upscale images between the two thresholds (40,000 ≤ area ≤ 600²). They are saved as-is.

## Design

### Constants (top of `store.py`, near `_MAX_DISPLAY_PX`)

```python
_MIN_DISPLAY_AREA = 40_000   # below this (px²) → upscale
_MIN_DISPLAY_SIDE = 300      # upscale target: both sides ≥ this
```

### `_probe_and_resize` change

Current logic (single branch — only downscales):

```python
w, h = im.size
long_side = max(w, h)
if long_side <= _MAX_DISPLAY_PX:
    return data, (w, h), False          # under cap → save as-is
scale = _MAX_DISPLAY_PX / long_side     # else → downscale
...
```

New logic (downscale unchanged; add upscale in the under-cap branch):

```python
w, h = im.size
long_side = max(w, h)
if long_side > _MAX_DISPLAY_PX:
    # downscale (unchanged)
    scale = _MAX_DISPLAY_PX / long_side
    new_size = (round(w * scale), round(h * scale))
    reason = "downscale"
elif w * h < _MIN_DISPLAY_AREA:
    # upscale: both sides ≥ _MIN_DISPLAY_SIDE, aspect preserved
    scale = _MIN_DISPLAY_SIDE / min(w, h)
    new_size = (round(w * scale), round(h * scale))
    reason = "upscale"
else:
    return data, (w, h), False          # in range → save as-is

im_resized = im.convert("RGB").resize(new_size, PILImage.LANCZOS)
buf = BytesIO()
im_resized.save(buf, format="JPEG", quality=85)
resized_bytes = buf.getvalue()
logger.info(
    f"[IMG-TRACE] PERSIST_RESIZE url={url or '<unknown>'} "
    f"from={w}x{h} to={new_size[0]}x{new_size[1]} "
    f"reason={reason} "
    f"max_px={_MAX_DISPLAY_PX} min_area={_MIN_DISPLAY_AREA} min_side={_MIN_DISPLAY_SIDE}"
)
return resized_bytes, new_size, True
```

Notes:
- The existing `PERSIST_RESIZE` event is reused (not a new event name); a `reason=downscale|upscale` field is added so a grep can tell the two apart. The `max_px=` field stays for downscale compatibility; `min_area=` / `min_side=` are added for upscale auditability.
- Both branches share the same LANCZOS + JPEG q85 re-encode path (de-duplicated vs. today's downscale-only code).
- Edge case: an image that is BOTH over-long-side AND under-area is impossible (long side > 600 ⇒ area > 600×1, but a 600×1 image has area 600 < 40000 — however such a degenerate sliver would be downscaled first since the `> _MAX_DISPLAY_PX` check comes first and wins). Order matters: downscale check first.

## Testing

`tests/images/test_persist_resize.py` (exists — Task 8's tests). Append:

1. **`test_undersized_image_upscaled_on_persist`** — a 150×120 image (area 18,000 < 40,000) is persisted. Assert the saved file's long-and-short sides are both ≥ 300, aspect ratio (150:120 = 5:4) is preserved (300×240), and the log carries `PERSIST_RESIZE ... reason=upscale`. *Fails against current `main` (today it saves 150×120 as-is).*
2. **`test_upscale_preserves_aspect_ratio_landscape`** — a 120×90 image upscales to 400×300 (scale = 300/90 = 3.33). Assert exact dims.
3. **`test_image_between_thresholds_unchanged`** — a 250×200 image (area 50,000, between 40,000 and 600²) is saved at native size, no `PERSIST_RESIZE` event. Guards the "don't touch the middle band" contract.
4. **`test_upscale_then_downscale_precedence`** — a 700×1 degenerate sliver (long side > 600 AND area < 40,000) downscales, does not upscale (downscale-check-first). Edge-case guard.

Existing Task 8 tests (`test_oversized_image_resized_on_persist`, `test_under_cap_image_not_resized`) must still pass — verify the refactored shared re-encode path didn't regress them.

**Regression gate:** `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ -q` green.

## Files

| File | Change |
|---|---|
| `src/local_deep_research/images/store.py` | Add `_MIN_DISPLAY_AREA` / `_MIN_DISPLAY_SIDE` constants; add upscale branch + refactor `_probe_and_resize` to shared re-encode path. |
| `tests/images/test_persist_resize.py` | Add the 4 tests above. |

## Success criteria

- Undersized image (< 40,000 px²) saved upscaled so both sides ≥ 300, aspect preserved.
- Mid-band image (40,000 ≤ area ≤ 600²) byte-identical behavior to today.
- Oversized downscale behavior unchanged.
- `tests/images/` green.
- Live: on next research run, a previously-tiny image persists at ≥300px and renders readable in WebUI + PDF; `PERSIST_RESIZE ... reason=upscale` events appear in the trace.
