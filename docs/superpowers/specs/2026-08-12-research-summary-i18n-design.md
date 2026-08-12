# Localize the Research-Summary Boilerplate (Localized in-Place)

**Date:** 2026-08-12
**Status:** Approved approach, pending implementation
**Scope:** Single-file bug fix in `report_generator.py`. NOT part of the image-pipeline-optimization plan or canonical-attach work — an independent i18n defect surfaced during their log review.

## Problem

`ReportGenerator._format_report` (`src/local_deep_research/report_generator.py:726-733`) builds the report's "research summary" chapter from a localized heading followed by **two hard-coded English sentences**:

```python
report_parts.append(headings["summary"])          # localized: "# 研究摘要" for zh-CN
report_parts.append(
    "This report was researched using an advanced search system."     # hard-coded EN
)
report_parts.append(
    "Research included targeted searches for each section and subsection."  # hard-coded EN
)
```

`headings` comes from `_get_chapter_headings()`, which localizes the **heading** by `report.language` (`zh-CN` → `# 研究摘要`). But the two body lines are English string literals with no localization path at all. Result: a Chinese report renders

```
# 研究摘要
This report was researched using an advanced search system.
Research included targeted searches for each section and subsection.
```

— a Chinese heading above English body text, directly contradicting the all-Chinese output the user requested. The function's own docstring (report_generator.py:708-710) states the design intent: *"Localized chapter scaffolding (TOC + Research Summary + Sources headings). Driven by report.language so the scaffolding matches the body language."* The summary body was missed when that scaffolding was localized.

## Goal

The research-summary body lines render in the report's language, matching the already-localized heading — so a `zh-CN` report shows Chinese summary text, not English.

## Approach chosen: in-place language branch (paradigm A)

The repo has two i18n conventions:

- **Paradigm A — in-place language branch:** `_get_chapter_headings()` (report_generator.py:112-148) maps `report.language` → a dict of localized strings (`zh-CN` → Chinese, fall through to English). Used for all report **scaffolding** text (TOC, summary heading, sources heading).
- **Paradigm B — gettext + translations/zh.json:** `from ..web.translations import _` (used in `error_handling/report_generator.py`), with English keys → Chinese values in `web/translations/zh.json` (2918 entries).

**Decision: Paradigm A.** The summary body is scaffolding text — it sits immediately next to `headings["summary"]`, is produced by the same function, and belongs to the same "chapter framework" the docstring describes. Putting it in `web/translations/zh.json` (Paradigm B) would introduce a `report_generator → web.translations` dependency direction for one piece of report scaffolding, inconsistent with how every other scaffolding string in this function is localized. A is the established, minimal, self-contained convention for exactly this location.

## Design

Extend `_get_chapter_headings()` to also return the summary body lines, then consume them in `_format_report`.

### Change 1 — `_get_chapter_headings()` return shape

The localized dict gains a `summary_lines` key holding the list of body strings. Current (report_generator.py:137-148):

```python
localized = {
    "zh-CN": {"toc": "# 目录", "summary": "# 研究摘要", "sources": "## 参考文献"},
}.get(lang)
return localized or {
    "toc": "# Table of Contents",
    "summary": "# Research Summary",
    "sources": "## Sources",
}
```

New:

```python
localized = {
    "zh-CN": {
        "toc": "# 目录",
        "summary": "# 研究摘要",
        "sources": "## 参考文献",
        "summary_lines": [
            "本报告使用高级搜索系统完成研究。",
            "研究过程中针对每个章节与子小节进行了定向检索。",
        ],
    },
}.get(lang)
return localized or {
    "toc": "# Table of Contents",
    "summary": "# Research Summary",
    "sources": "## Sources",
    "summary_lines": [
        "This report was researched using an advanced search system.",
        "Research included targeted searches for each section and subsection.",
    ],
}
```

This keeps the English text as the **fall-through default** (so unknown locales and English get exactly today's output — no behavior change for non-zh-CN reports), and `_get_chapter_headings`'s existing "unknown locale falls through to English" contract is preserved.

The method docstring's "Returns a dict with keys `toc`, `summary`, `sources`." line is updated to add `summary_lines`.

### Change 2 — consume `summary_lines` in `_format_report`

Current (report_generator.py:727-733):

```python
report_parts.append(headings["summary"])
report_parts.append(
    "This report was researched using an advanced search system."
)
report_parts.append(
    "Research included targeted searches for each section and subsection."
)
```

New:

```python
report_parts.append(headings["summary"])
report_parts.extend(headings["summary_lines"])
```

## Non-goals

- Do NOT localize any other scaffolding text in this change. Only the two summary body lines. TOC entries (`section["name"]`, `subsection["name"]`, `subsection["purpose"]` at lines 716-721) are LLM-generated in the report language already — out of scope.
- Do NOT switch the heading localization to gettext. `_get_chapter_headings` stays paradigm A.
- Do NOT touch `web/translations/zh.json`. Paradigm B is not used here.

## Testing

Tests live alongside existing report-generator tests. Conventions: pytest, `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest <paths> -q`.

1. **`test_summary_lines_localized_for_zh_cn`** — build the heading set for `report.language="zh-CN"`, assert `headings["summary_lines"]` contains Chinese text and does NOT contain "advanced search system".
2. **`test_summary_lines_english_default_for_unknown_locale`** — `report.language="fr-FR"` (or any non-`zh-CN`), assert `headings["summary_lines"]` equals the two English sentences verbatim (fall-through contract preserved).
3. **`test_format_report_renders_localized_summary_body`** — drive `_format_report` (or the report path that exercises it) with `report.language="zh-CN"` and assert the rendered summary region contains the Chinese body lines, not the English ones.

If the existing report-generator test fixtures make driving `_format_report` end-to-end heavy, tests 1+2 (unit-level on `_get_chapter_headings`) are the load-bearing ones; test 3 is desirable but optional if wiring is disproportionate. Note this in the plan.

**Regression gate:** `LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/report_generator/ tests/web/test_report*.py -q` (scope to whatever exists) must be green. Existing tests that assert the English summary text (if any) will need updating to assert it only for the English locale — search for `"advanced search system"` in tests/ first.

## Files

| File | Change |
|---|---|
| `src/local_deep_research/report_generator.py` | Extend `_get_chapter_headings` (~137-148) with `summary_lines`; consume it in `_format_report` (727-733); update docstring. |
| `tests/report_generator/` (new or existing) | Add the 2-3 tests above. |

## Success criteria

- `zh-CN` report's research-summary body renders in Chinese (heading + body both Chinese).
- Non-`zh-CN` report's summary body is byte-identical to today (English) — no behavior change.
- Test suite green.
- Live container verification on next `zh-CN` detailed-mode run: the summary region in the rendered report shows Chinese body text (probe: visual / report body, not an IMG-TRACE event — this is a non-image fix).
