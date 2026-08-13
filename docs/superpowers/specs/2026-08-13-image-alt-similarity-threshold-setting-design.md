# Image Alt–Section Similarity Threshold — User-Configurable Setting

**Date:** 2026-08-13
**Status:** Approved design, pending implementation plan
**Scope:** Single new user setting + its end-to-end wiring. Independent of the image-pipeline-optimization plan and the canonical-attach work — a configurability gap surfaced during the `30c5a901` candidate-drops analysis (high-relevance cross-lingual alts like `南翔馒头店 实景照片` scored 0.600 and were dropped by the 0.6 gate).

## Problem

The image pipeline keeps a candidate image only when its alt text's cosine similarity to its section's phrase (heading + parent heading + entities, per Task 6 of the optimization plan) clears a fixed threshold. That threshold is a hard-coded constant:

- `src/local_deep_research/images/semantic_matcher.py:51` — `DEFAULT_THRESHOLD = 0.6`
- Consumed as the default of `enhance_report_with_images(..., alt_similarity_threshold: float = _DEFAULT_THRESHOLD)` in `src/local_deep_research/images/postprocessing.py:215`.
- **No caller ever overrides it.** Both call sites (`research_service.py:1769` and `:2170`) unpack `**img_args`, and `_open_image_enhancer_session()` (the sole builder of `img_args`) never sets `alt_similarity_threshold` — so the parameter always takes the 0.6 default.

Consequence: the user cannot tune precision/recall for image adoption. The `30c5a901` analysis showed this is exactly the knob that matters — cross-lingual alts (EN alt vs ZH section) consistently score 0.50–0.60 (see `semantic-matcher-crosslingual-threshold` memory), landing just under or at the 0.6 gate, so highly relevant images get dropped with no recourse short of editing source.

The threshold must become a user setting, with a hard floor so it cannot be set so low that image adoption becomes noise.

## Goal

Promote `alt_similarity_threshold` to a user-configurable setting `report.image_alt_similarity_threshold`:

- **Default 0.6** (identical to the current hard-coded value — configuring the setting changes no current behavior).
- **Floor 0.45** (clamped; the setting cannot take effect below 0.45). Chosen because the cross-lingual floor observed in data is ~0.44–0.54; below 0.45 the matcher admits too many unrelated images.
- Exposed in the WebUI **报告 / Report** tab alongside the existing `report.image_*` settings.
- **Bilingual (EN + zh-CN) tips**, using the project's existing `i18n.t(setting.description)` mechanism.
- `min_margin` is explicitly **out of scope** — it is currently `PAUSED` in code (a no-op `pass` at `semantic_matcher.py:355-360`) so exposing it would sell the user a knob that does nothing.

## Non-Goals

- No change to the comparison operator (`score >= threshold` stays).
- No change to `DEFAULT_THRESHOLD = 0.6` as the *function* default — it remains the fallback if a caller passes nothing. The setting overrides it at the call site, not inside `semantic_matcher`.
- No change to `min_margin`, `DEFAULT_MIN_MARGIN`, or the paused `ambiguous_match` path.
- No new IMG-TRACE event for the normal read path; only a `SETTING_CLAMP` event when the floor has to clamp (observable, low-volume).

## Architecture

Four surgical edits, one per file. The data flow is single-direction and has no feedback:

```
default_settings.json          ← ①  declares the setting (key, default 0.6,
   (report.image_alt_sim...)       min 0.45, max 1.0, step 0.05, EN tips)
        │
        ▼
   (user edits in WebUI 报告 tab; settings.js renders it via the
    existing generic settings renderer, which already honors
    min_value/max_value/step and i18n.t(name+description))
        │
        ▼
   persisted in per-user settings store (no code change — generic)
        │
        ▼
research_service.py            ← ③  _open_image_enhancer_session() reads the
   (_open_image_enhancer_session)   setting via get_setting_from_snapshot,
                                    clamps to floor 0.45 (logs SETTING_CLAMP
                                    if it clamped), puts it in args dict
        │
        ▼ (img_args unpacked at both call sites via **img_args)
postprocessing.py              ← no edit (param already exists with default)
   (enhance_report_with_images)    threshold = alt_similarity_threshold  [line 326]
                                    if score >= threshold:               [line 440]
        │
        ▼
semantic_matcher.py            ← ② docstring/comment touch only (DEFAULT_THRESHOLD
   (DEFAULT_THRESHOLD)             is still the function-level default; note that
                                    the report path now overrides it via the setting)
```

Because the parameter `alt_similarity_threshold` already exists end-to-end (defined on `enhance_report_with_images`, consumed at `postprocessing.py:326/440`), **the only plumbing missing is reading the setting and passing it in.** No new parameter, no new signature.

## Components

### ① Setting declaration — `src/local_deep_research/defaults/default_settings.json`

Insert a new top-level (dotted-key) entry immediately after `report.image_vision_cap` (it is the last `report.image_*` key, so the new key groups cleanly). Modeled verbatim on `report.searches_per_section` (a float with `min_value`/`max_value`/`step`) and `report.image_vision_cap` (same `category`/`type`):

```json
"report.image_alt_similarity_threshold": {
  "category": "report_parameters",
  "description": "Minimum cosine similarity (0–1) between an image's alt text and its section heading+content required for the image to be kept in the report. Lower = more images but more mismatches; higher = fewer, more precise images. Clamped to a floor of 0.45. Default 0.6.",
  "editable": true,
  "max_value": 1.0,
  "min_value": 0.45,
  "name": "Image alt-section similarity threshold",
  "options": null,
  "step": 0.05,
  "type": "REPORT",
  "ui_element": "number",
  "value": 0.6,
  "visible": true
}
```

- `min_value: 0.45` is the **first** floor guard — the WebUI number input will reject values below 0.45 at submit (the generic renderer honors `min_value`).
- `value: 0.6` matches the current hard code; configuring this setting without changing the value reproduces today's behavior exactly.

### ② Bilingual tips — `src/local_deep_research/web/translations/zh.json`

The settings UI translates every setting's `name` and `description` via `i18n.t(...)` (`src/local_deep_research/web/static/js/components/settings.js:2275-2276`), keyed by the EN string. Add two entries to `zh.json`:

```json
"Image alt-section similarity threshold": "图片 alt 与章节相似度阈值",
"Minimum cosine similarity (0–1) between an image's alt text and its section heading+content required for the image to be kept in the report. Lower = more images but more mismatches; higher = fewer, more precise images. Clamped to a floor of 0.45. Default 0.6.": "图片 alt 文本与其所在「章节标题 + 章节内容」的余弦相似度达到该值（0–1）才会被采纳进报告。调低 = 收录更多图片但误配增多；调高 = 图片更少更精准。下限 0.45（低于此值会被钳制到 0.45）。默认 0.6。"
```

The EN strings MUST match the `name`/`description` in ① character-for-character — `i18n.t` falls back to the key itself (the EN string) when no translation is present, so EN always works; the zh entry adds the Chinese rendering.

### ③ Read + clamp + pass-through — `src/local_deep_research/web/services/research_service.py`

In `_open_image_enhancer_session(username, settings_snapshot)` (starts `research_service.py:374`), immediately before the `args = dict(` at line 427, insert the read + clamp. This is the **second** floor guard (the runtime authority — even if the DB holds a value below 0.45 from an older client or a direct DB edit, the runtime never honors it):

```python
    alt_similarity_threshold = get_setting_from_snapshot(
        "report.image_alt_similarity_threshold", 0.6,
        settings_snapshot=settings_snapshot,
    )
    # Hard floor: below 0.45 the matcher admits too many unrelated
    # images (cross-lingual alt/section pairs bottom out ~0.44).
    # Clamp at the read site so every caller is protected even if the
    # per-user DB value was set below the floor by an older client.
    _ALT_SIMILARITY_FLOOR = 0.45
    if alt_similarity_threshold < _ALT_SIMILARITY_FLOOR:
        logger.info(
            f"[IMG-TRACE] SETTING_CLAMP "
            f"report.image_alt_similarity_threshold "
            f"requested={alt_similarity_threshold} "
            f"floor={_ALT_SIMILARITY_FLOOR}"
        )
        alt_similarity_threshold = _ALT_SIMILARITY_FLOOR
```

Then add one key to the `args = dict(...)` block (line 427) so it flows into both `enhance_report_with_images(**img_args)` call sites:

```python
        alt_similarity_threshold=alt_similarity_threshold,
```

`logger` is already imported (`from loguru import logger` at `research_service.py:10`); `get_setting_from_snapshot` is already imported at the top of the function body (the local `from ...config.thread_settings import get_setting_from_snapshot` at `research_service.py:383`). No new imports. The insertion point is clean — the line immediately before `args = dict(` is a blank line following the `firecrawl_client` try/except block, so the read+clamp block does not split any existing construct.

### ④ Doc note — `src/local_deep_research/images/semantic_matcher.py`

Update only the comment on `DEFAULT_THRESHOLD` (line 51) to record that the report path now overrides it via the setting; the value stays `0.6` (it is still the legitimate function-level default for any non-report caller):

```python
# Function-level default. The report path overrides this via the
# report.image_alt_similarity_threshold user setting (read in
# _open_image_enhancer_session); non-report callers still get 0.6.
DEFAULT_THRESHOLD = 0.6
```

No behavioral change.

## Data Flow

1. User opens WebUI → 报告 tab → generic renderer reads the new setting from the API (which serves it from `default_settings.json` merged with the user's DB overrides).
2. Renderer draws a number input with `min=0.45 max=1 step=0.05` and the i18n-translated name + description (EN or zh per `app.language`).
3. On save, the value persists to the user's settings store (generic path, unchanged).
4. At research time, `_open_image_enhancer_session` reads the value, clamps to ≥0.45 (logging `SETTING_CLAMP` if it clamped), and puts it in `img_args`.
5. Both `enhance_report_with_images(**img_args)` call sites receive it as `alt_similarity_threshold`; `postprocessing.py:326` assigns `threshold = alt_similarity_threshold` and `:440` applies `score >= threshold`.

## Error Handling

- **Value below 0.45 in DB** (older client / direct edit): runtime clamps to 0.45, logs `SETTING_CLAMP`. Research proceeds normally.
- **Setting absent from snapshot** (older snapshot before this setting existed): `get_setting_from_snapshot` returns the `0.6` default → behavior identical to today. No KeyError possible (the helper takes a default).
- **Non-numeric / garbage in DB**: out of scope for this change — `get_setting_from_snapshot` already returns whatever is stored; the existing `min_value` UI guard prevents new garbage, and a pre-existing garbage value is a pre-existing problem unaffected by this edit. (If we wanted to defend against it we would wrap in `float(...)`, but that is a separate hardening task and not named by the requirement.)

## Testing

New test file `tests/web/test_image_threshold_setting.py`. The contract is purely about `_open_image_enhancer_session`'s output `args` (no DB, no network — the snapshot is a plain dict, the function opens a user DB session via `get_user_db_session` which we mock):

```python
def test_threshold_defaults_to_0_6(monkeypatch):
    # snapshot has no report.image_alt_similarity_threshold key
    # → args["alt_similarity_threshold"] == 0.6

def test_threshold_read_from_setting(monkeypatch):
    # snapshot sets report.image_alt_similarity_threshold = 0.5
    # → args["alt_similarity_threshold"] == 0.5

def test_threshold_clamped_to_floor(monkeypatch):
    # snapshot sets report.image_alt_similarity_threshold = 0.3
    # → args["alt_similarity_threshold"] == 0.45
    # and a SETTING_CLAMP line is logged
```

The `get_user_db_session` context manager is mocked to yield a dummy session (the threshold logic runs before the `with`, so this is straightforward). Existing deferred-fill tests are unaffected (they call `_deferred_image_fill` directly, not `_open_image_enhancer_session`).

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py -q`

## Behavior Preservation

- **Default (setting unset) = today's behavior.** `value: 0.6` + `get_setting_from_snapshot(..., 0.6, ...)` + `DEFAULT_THRESHOLD = 0.6` all agree. A research run with the setting untouched selects exactly the same images as before this change.
- **No attach-match logic change** (the anti-mismatch red line from the optimization plan holds — this is a threshold knob, not a match-rule change).
- **`min_margin` untouched** — stays paused, stays 0.05, stays unconfigured.

## File Summary

| File | Edit |
|---|---|
| `src/local_deep_research/defaults/default_settings.json` | +1 entry (`report.image_alt_similarity_threshold`) after `report.image_vision_cap` |
| `src/local_deep_research/web/translations/zh.json` | +2 entries (name + description zh translation) |
| `src/local_deep_research/web/services/research_service.py` | read+clamp block before `args = dict(` (line 427) in `_open_image_enhancer_session`; +1 key in `args` |
| `src/local_deep_research/images/semantic_matcher.py` | comment-only on `DEFAULT_THRESHOLD` (line 51) |
| `tests/web/test_image_threshold_setting.py` | new — 3 tests |
