# Image Alt–Section Similarity Threshold Setting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the hard-coded `alt_similarity_threshold` (0.6) to a user-configurable setting `report.image_alt_similarity_threshold` with a 0.45 floor and bilingual (EN/zh-CN) tips, changing no default behavior.

**Architecture:** Four surgical edits across three subsystems (settings registry, i18n, runtime read-site) plus one doc-only comment and one test file. The `alt_similarity_threshold` parameter already exists end-to-end on `enhance_report_with_images` and is consumed in `postprocessing.py`; the only missing plumbing is reading the setting, clamping to the floor, and passing it through `_open_image_enhancer_session`'s `args` dict. Floor is enforced twice: UI `min_value` (submit-time) + runtime clamp (authority).

**Tech Stack:** Python 3.14, pytest 9, loguru, uv-managed venv. WebUI settings UI is generic (renders any `default_settings.json` entry); i18n is key-as-English-string via `i18n.t`.

**Spec:** `docs/superpowers/specs/2026-08-13-image-alt-similarity-threshold-setting-design.md` (commit `6dbd19b8`).

## Global Constraints

- Branch: `main` is the only active branch. Run `git rev-parse --abbrev-ref HEAD` before every commit; if it does not print `main`, STOP.
- No background git. All git operations foreground/blocking only.
- After every commit run `git log --oneline -3` and confirm the new commit is at HEAD on `main`.
- Test command (host, uv venv — the container image has no pytest and must NOT be mutated):
  `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest <paths> -q`
  If `.venv` is missing, create it once with `uv sync --group dev`.
- Surgical changes only: touch only the lines these tasks name. Do not reformat, rename, or "improve" adjacent code. Match the existing JSON indentation (8 spaces inside entries) and Python style exactly.
- Default value is `0.6` everywhere (current hard-coded value) — this change must NOT alter any current research's image selection.
- Floor is `0.45`. The setting must never take effect below 0.45.
- `min_margin` is OUT OF SCOPE — do not touch it (it is PAUSED in code; configuring it would expose a no-op knob).
- IMG-TRACE field vocabulary is fixed; the one new event this plan adds is `SETTING_CLAMP` (uses plain `key=value` fields, not the five-key image schema, because it is a setting event, not an image event).
- Deployment: source is hot-mounted read-only into the container; source edits apply on container restart. No image rebuild for these changes.

## File Structure

| File | Responsibility | Touched by |
|---|---|---|
| `src/local_deep_research/defaults/default_settings.json` | Flat dotted-key settings registry; declares keys + UI metadata | Task 1 |
| `src/local_deep_research/web/translations/zh.json` | Chinese translations, keyed by the English source string | Task 2 |
| `src/local_deep_research/web/services/research_service.py` | `_open_image_enhancer_session` builds `img_args` from settings | Task 3 |
| `src/local_deep_research/images/semantic_matcher.py` | `DEFAULT_THRESHOLD` constant + doc note | Task 4 (comment only) |
| `tests/web/test_image_threshold_setting.py` | Contract tests for the read + clamp behavior | Task 1 (bootstrap), Task 3 (behavior) |

The four production edits are independent in file but ordered by dependency: Task 1 declares the key (so Task 3 can read it and Task 3's tests can assert on it), Task 2 adds translations (parallel to Task 1, but kept after it for a clean review order), Task 4 is a doc-only touch.

---

### Task 1: Declare the setting in `default_settings.json`

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json` (insert one new top-level entry immediately after the `report.image_vision_cap` entry)
- Create: `tests/web/test_image_threshold_setting.py` (bootstrap the test file + the default-value test)

**Interfaces:**
- Consumes: nothing.
- Produces: a new dotted-key `report.image_alt_similarity_threshold` in the settings registry with `value: 0.6`, `min_value: 0.45`, `max_value: 1.0`, `step: 0.05`. This key is what Task 3 reads and what the WebUI generic renderer displays.

- [ ] **Step 1: Write the failing test (bootstrap file + default-value test)**

Create `tests/web/test_image_threshold_setting.py`:

```python
"""Contract tests for the report.image_alt_similarity_threshold setting.

Covers: the default value is declared, the read-site picks it up, and
the 0.45 floor is enforced at runtime.
"""

import json

import pytest

from local_deep_research.defaults import default_settings as _defaults_pkg  # noqa: F401


def _load_defaults():
    """Load the bundled default_settings.json as-shipped."""
    import local_deep_research.defaults as pkg
    from pathlib import Path

    path = Path(pkg.__file__).parent / "default_settings.json"
    return json.loads(path.read_text())


def test_setting_declared_with_default_0_6():
    d = _load_defaults()
    key = "report.image_alt_similarity_threshold"
    assert key in d, f"{key} must be declared in default_settings.json"
    entry = d[key]
    assert entry["value"] == 0.6, "default must be 0.6 (no behavior change)"
    assert entry["min_value"] == 0.45, "UI floor must be 0.45"
    assert entry["max_value"] == 1.0
    assert entry["category"] == "report_parameters"
    assert entry["ui_element"] == "number"
    assert entry["visible"] is True
    assert entry["editable"] is True
    assert entry["type"] == "REPORT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py::test_setting_declared_with_default_0_6 -q`
Expected: FAIL with `AssertionError: 'report.image_alt_similarity_threshold' must be declared in default_settings.json`.

- [ ] **Step 3: Add the setting entry**

In `src/local_deep_research/defaults/default_settings.json`, locate the `report.image_vision_cap` entry (it is the last `report.image_*` key, 8-space-indented inside the entry). Immediately after its closing `}` and the comma, insert this new entry (matching the surrounding 8-space internal indentation exactly):

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
    },
```

The entry text to anchor on (insert immediately AFTER this block, before the next key):

```json
    "report.image_vision_cap": {
        "category": "report_parameters",
        "description": "Hard cap on how many alt-less images the vision model will describe in a single research run. Caps vision cost per research.",
        "editable": true,
        "max_value": 100,
        "min_value": 1,
        "name": "Vision alt-fill cap (max images described)",
        "options": null,
        "step": 1,
        "type": "REPORT",
        "ui_element": "number",
        "value": 10,
        "visible": true
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py::test_setting_declared_with_default_0_6 -q`
Expected: PASS.

- [ ] **Step 5: Validate the JSON is still well-formed (parser-level regression)**

Run: `.venv/bin/python -c "import json; json.load(open('src/local_deep_research/defaults/default_settings.json')); print('JSON OK')"`
Expected: prints `JSON OK`. (If this prints an error, the comma/brace placement is wrong — fix before committing.)

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/defaults/default_settings.json tests/web/test_image_threshold_setting.py
git commit -m "feat(settings): declare report.image_alt_similarity_threshold

New user setting (default 0.6, floor 0.45, max 1.0, step 0.05) that
controls the alt-vs-section cosine-similarity gate for image adoption.
Default 0.6 preserves current behavior. UI min_value=0.45 is the first
floor guard; runtime clamp (next task) is the authority."
git log --oneline -3
```

---

### Task 2: Add the zh-CN translations

**Files:**
- Modify: `src/local_deep_research/web/translations/zh.json` (add two key/value pairs)

**Interfaces:**
- Consumes: the exact English strings from Task 1's `name` and `description` (the `i18n.t` lookup is keyed by these strings).
- Produces: Chinese rendering of the new setting's name and description when `app.language` is zh.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_image_threshold_setting.py`:

```python
def test_zh_translation_present_for_name_and_description():
    """Both the setting name and description must have a zh.json entry,
    keyed by the exact English string from default_settings.json."""
    d = _load_defaults()
    entry = d["report.image_alt_similarity_threshold"]
    name_en = entry["name"]
    desc_en = entry["description"]

    import local_deep_research.web.translations as tr_pkg
    from pathlib import Path

    zh_path = Path(tr_pkg.__file__).parent / "zh.json"
    zh = json.loads(zh_path.read_text())

    assert name_en in zh, (
        f"name string missing from zh.json: {name_en!r}"
    )
    assert desc_en in zh, (
        f"description string missing from zh.json: {desc_en!r}"
    )
    # Guard against the classic copy-paste error: both must map to
    # non-empty Chinese (CJK) text, and must be DISTINCT values.
    assert zh[name_en] and zh[desc_en]
    assert zh[name_en] != zh[desc_en], "name and description translations must differ"
    assert any("一" <= ch <= "鿿" for ch in zh[name_en]), \
        "name translation must contain Chinese characters"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py::test_zh_translation_present_for_name_and_description -q`
Expected: FAIL with `AssertionError: name string missing from zh.json: 'Image alt-section similarity threshold'`.

- [ ] **Step 3: Add the two zh entries**

In `src/local_deep_research/web/translations/zh.json`, the file is a flat `{"english": "中文"}` object. Add these two entries anywhere in the object (the conventional spot is near other "Vision"/"report" settings, but JSON object order is not semantically significant — place them adjacent to keep reviewers oriented). Match the existing indentation (the file uses 2-space indent with `"<en>": "<zh>"` lines).

```json
  "Image alt-section similarity threshold": "图片 alt 与章节相似度阈值",
  "Minimum cosine similarity (0–1) between an image's alt text and its section heading+content required for the image to be kept in the report. Lower = more images but more mismatches; higher = fewer, more precise images. Clamped to a floor of 0.45. Default 0.6.": "图片 alt 文本与其所在「章节标题 + 章节内容」的余弦相似度达到该值（0–1）才会被采纳进报告。调低 = 收录更多图片但误配增多；调高 = 图片更少更精准。下限 0.45（低于此值会被钳制到 0.45）。默认 0.6。",
```

CRITICAL: the English keys above MUST match the `name` and `description` fields from Task 1 character-for-character (including the en-dash `–` in `(0–1)`, the `+` in `heading+content`, and the `0.45` / `0.6` digits). If Task 1's strings were edited, copy them verbatim from the committed `default_settings.json` before writing these keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py::test_zh_translation_present_for_name_and_description -q`
Expected: PASS.

- [ ] **Step 5: Validate zh.json is still well-formed**

Run: `.venv/bin/python -c "import json; json.load(open('src/local_deep_research/web/translations/zh.json')); print('JSON OK')"`
Expected: prints `JSON OK`.

- [ ] **Step 6: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/translations/zh.json tests/web/test_image_threshold_setting.py
git commit -m "i18n(zh): translate image alt-section similarity threshold

Add zh-CN name + description for report.image_alt_similarity_threshold,
keyed by the English source strings (existing i18n.t mechanism). Floor
0.45 stated explicitly in the Chinese description per requirement."
git log --oneline -3
```

---

### Task 3: Read, clamp, and pass the setting through `_open_image_enhancer_session`

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py:427` (insert read+clamp block before `args = dict(`; add one key inside `args = dict(`)
- Test: `tests/web/test_image_threshold_setting.py` (append behavior tests)

**Interfaces:**
- Consumes: `get_setting_from_snapshot(key, default, settings_snapshot=...)` (already imported locally at `research_service.py:383`); `logger` (already `from loguru import logger` at `research_service.py:10`).
- Produces: `args["alt_similarity_threshold"]` flowing into both `enhance_report_with_images(**img_args)` call sites (`research_service.py:1769`, `:2170`), which already accept the param (`postprocessing.py:215`) and consume it at `postprocessing.py:326` (`threshold = alt_similarity_threshold`) and `:440` (`if score >= threshold`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_image_threshold_setting.py`:

```python
from local_deep_research.web.services import research_service


def _open_args(snapshot):
    """Drive _open_image_enhancer_session as a generator and return its
    args dict. get_user_db_session is mocked so no real DB is opened."""
    import contextlib

    @contextlib.contextmanager
    def _fake_db(_username):
        yield object()  # dummy session

    saved = research_service.get_user_db_session
    research_service.get_user_db_session = _fake_db
    try:
        gen = research_service._open_image_enhancer_session(
            "testuser", settings_snapshot=snapshot
        )
        args, _session = next(gen)
        return args
    finally:
        research_service.get_user_db_session = saved


def test_threshold_defaults_to_0_6_when_unset():
    # No report.image_alt_similarity_threshold key in the snapshot.
    args = _open_args({"report.enable_images": True})
    assert args["alt_similarity_threshold"] == 0.6


def test_threshold_read_from_setting():
    args = _open_args({
        "report.enable_images": True,
        "report.image_alt_similarity_threshold": 0.5,
    })
    assert args["alt_similarity_threshold"] == 0.5


def test_threshold_clamped_to_floor(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        args = _open_args({
            "report.enable_images": True,
            "report.image_alt_similarity_threshold": 0.3,
        })
    # Clamped to the 0.45 floor even though 0.3 was requested.
    assert args["alt_similarity_threshold"] == 0.45
    # A SETTING_CLAMP trace line is emitted.
    assert any(
        "SETTING_CLAMP" in rec.getMessage()
        and "report.image_alt_similarity_threshold" in rec.getMessage()
        for rec in caplog.records
    ), "expected a SETTING_CLAMP log when the value is below the floor"


def test_threshold_at_floor_is_not_clamped():
    # Exactly 0.45 must NOT clamp (the gate is < floor, not <= floor).
    args = _open_args({
        "report.enable_images": True,
        "report.image_alt_similarity_threshold": 0.45,
    })
    assert args["alt_similarity_threshold"] == 0.45
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py -q -k "defaults_to_0_6_when_unset or read_from_setting or clamped_to_floor or at_floor_is_not"`
Expected: FAIL — `KeyError: 'alt_similarity_threshold'` (the key is not yet added to `args`).

- [ ] **Step 3: Add the read + clamp block**

In `src/local_deep_research/web/services/research_service.py`, inside `_open_image_enhancer_session(username, settings_snapshot)`, the `args = dict(` begins at line 427. The line immediately before it is a blank line following the `firecrawl_client` try/except block. Insert this block in that blank line (between the try/except and `args = dict(`):

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

- [ ] **Step 4: Add the key to `args = dict(`**

In the same function, the `args = dict(...)` block (now pushed down a few lines by Step 3) currently ends with:

```python
        firecrawl_client=firecrawl_client,
        enable_images=enable_images,
    )
```

Add one key before the closing paren:

```python
        firecrawl_client=firecrawl_client,
        enable_images=enable_images,
        alt_similarity_threshold=alt_similarity_threshold,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_image_threshold_setting.py -q`
Expected: all PASS (the 4 new behavior tests + Task 1's declare test + Task 2's zh test).

- [ ] **Step 6: Run a regression on the deferred-fill suite (the heaviest existing consumer of this module)**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/test_deferred_image_fill.py -q`
Expected: all pass. These tests call `_deferred_image_fill` directly (not `_open_image_enhancer_session`), so they should be unaffected; this run confirms no accidental import-time breakage.

- [ ] **Step 7: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/web/services/research_service.py tests/web/test_image_threshold_setting.py
git commit -m "feat(images): read image_alt_similarity_threshold from settings

_open_image_enhancer_session now reads report.image_alt_similarity_threshold
(default 0.6) and passes it through img_args to enhance_report_with_images.
Values below the 0.45 floor are clamped at the read site and logged as
[IMG-TRACE] SETTING_CLAMP — the runtime authority backing the UI min_value."
git log --oneline -3
```

---

### Task 4: Doc note on `DEFAULT_THRESHOLD`

**Files:**
- Modify: `src/local_deep_research/images/semantic_matcher.py:51` (comment only)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (comment-only; the value stays `0.6`).

- [ ] **Step 1: Read the current line to anchor the edit**

Run: `grep -n "DEFAULT_THRESHOLD = 0.6" src/local_deep_research/images/semantic_matcher.py`
Expected: prints one line (line 51) reading `DEFAULT_THRESHOLD = 0.6`. Note any comment currently on the lines immediately above it.

- [ ] **Step 2: Update the comment (value unchanged)**

In `src/local_deep_research/images/semantic_matcher.py`, change the comment immediately above `DEFAULT_THRESHOLD = 0.6` (line 51) so it records that the report path now overrides this via the setting. The value `0.6` MUST stay. If there is no preceding comment, add these three lines immediately before the `DEFAULT_THRESHOLD = 0.6` line:

```python
# Function-level default. The report path overrides this via the
# report.image_alt_similarity_threshold user setting (read in
# _open_image_enhancer_session); non-report callers still get 0.6.
DEFAULT_THRESHOLD = 0.6
```

(If a comment already exists on those lines, replace it with the three lines above. Do not change `DEFAULT_THRESHOLD = 0.6` itself.)

- [ ] **Step 3: Verify no behavior change (the constant still equals 0.6)**

Run: `.venv/bin/python -c "from local_deep_research.images.semantic_matcher import DEFAULT_THRESHOLD; assert DEFAULT_THRESHOLD == 0.6, DEFAULT_THRESHOLD; print('OK', DEFAULT_THRESHOLD)"`
Expected: prints `OK 0.6`.

- [ ] **Step 4: Run the semantic-matcher tests for regression**

Run: `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/images/ -q`
Expected: all pass. (Comment-only change; this run guards against an accidental value edit.)

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print: main
git add src/local_deep_research/images/semantic_matcher.py
git commit -m "docs(images): note DEFAULT_THRESHOLD is overridden by setting

The report image path now overrides the 0.6 function default via the
report.image_alt_similarity_threshold user setting. Non-report callers
still get 0.6. Comment-only; no behavior change."
git log --oneline -3
```

---

## Self-Review

**1. Spec coverage:** Each spec component maps to a task:
- Setting declaration (spec ①) → Task 1 ✓
- Bilingual tips (spec ②) → Task 2 ✓
- Read + clamp + pass-through (spec ③) → Task 3 ✓
- Doc note (spec ④) → Task 4 ✓
- Testing (spec "Testing") → Task 1 (declare), Task 3 (behavior) ✓
- Behavior preservation (default 0.6, no attach-logic change) → Global Constraints + Task 3 default test ✓
- `min_margin` out of scope → Global Constraints ✓
- Floor 0.45 (UI + runtime clamp) → Task 1 `min_value`, Task 3 clamp + SETTING_CLAMP ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"add error handling"/"similar to". Every code step shows the exact code; the JSON anchor text and Python anchor signatures are reproduced verbatim. The one place (Task 4 Step 2) that says "if a comment already exists, replace it" is an explicit conditional, not a placeholder — the engineer runs the grep in Step 1 to know which branch applies.

**3. Type consistency:**
- `alt_similarity_threshold` (float) — Task 3 produces it as a dict key; both call sites unpack it into `enhance_report_with_images(alt_similarity_threshold: float = ...)` (already declared `postprocessing.py:215`). ✓
- `_ALT_SIMILARITY_FLOOR = 0.45` — local constant in `_open_image_enhancer_session`, referenced once in the clamp. ✓
- `report.image_alt_similarity_threshold` — identical dotted-key string in Task 1 (declaration), Task 2 (no, zh keys are the name/description strings, not the dotted key — verified the test asserts on `name`/`description`, not the key), Task 3 (read). The read key in Task 3 (`"report.image_alt_similarity_threshold"`) matches the declared key in Task 1 exactly. ✓
- English i18n keys in Task 2 (`name` + `description`) match Task 1's strings — Task 2 Step 3 has a CRITICAL note + Task 2's test asserts membership of the exact strings read from the committed JSON, so a drift fails the test. ✓
- `SETTING_CLAMP` event name — Task 3 test asserts `"SETTING_CLAMP" in rec.getMessage()` and Task 3 implementation emits exactly `"[IMG-TRACE] SETTING_CLAMP "`. ✓

**Cross-task dependency note:** Task 1 must land before Task 2 and Task 3 (both reference the committed English strings / the declared key). Task 4 is independent (comment-only) and can run in any order but is sequenced last for narrative cleanliness. All four tasks are independently committable.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-image-alt-similarity-threshold-setting.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
