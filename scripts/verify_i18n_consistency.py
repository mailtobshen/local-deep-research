#!/usr/bin/env python3
"""
Verify i18n translation file consistency. Catches the bug class that bit us:

  - en.json / zh.json have over-escaped keys (literal "\'" or '\"' or "\\\\"
    where the actual JS/Python string literal would unescape to ' / " / \\).
    Such keys NEVER match runtime i18n.t(...) lookups; they silently fall
    back to the lookup key (so en users see English, zh users see English
    instead of Chinese — a real regression).

  - en.json and zh.json key sets diverge.

Exit code:
  0 = clean
  1 = mismatches found
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EN_PATH = REPO / "src/local_deep_research/web/translations/en.json"
ZH_PATH = REPO / "src/local_deep_research/web/translations/zh.json"


def looks_over_escaped(s: str) -> bool:
    """A translation key should never contain a literal '\\' followed by
    a quote or another backslash — those are JS/Python escape sequences
    that should have been resolved at parse time, not stored verbatim."""
    return "\\'" in s or '\\"' in s or "\\\\" in s


def main() -> int:
    with open(EN_PATH) as f:
        en = json.load(f)
    with open(ZH_PATH) as f:
        zh = json.load(f)

    errors: list[str] = []

    # Check 1: over-escaped keys
    for label, data in (("en.json", en), ("zh.json", zh)):
        bad = [k for k in data if looks_over_escaped(k)]
        if bad:
            errors.append(
                f"{label}: {len(bad)} over-escaped key(s) — literal \\' / \\\" / \\\\ "
                f"in lookup keys will never match runtime calls:"
            )
            for k in bad[:5]:
                errors.append(f"    {k!r}")
            if len(bad) > 5:
                errors.append(f"    ... and {len(bad) - 5} more")

    # Check 2: key set parity
    missing_in_zh = sorted(set(en) - set(zh))
    missing_in_en = sorted(set(zh) - set(en))
    if missing_in_zh:
        errors.append(
            f"{len(missing_in_zh)} key(s) in en.json missing from zh.json "
            f"(zh users will see English fallback):"
        )
        for k in missing_in_zh[:5]:
            errors.append(f"    {k!r}")
        if len(missing_in_zh) > 5:
            errors.append(f"    ... and {len(missing_in_zh) - 5} more")
    if missing_in_en:
        errors.append(
            f"{len(missing_in_en)} key(s) in zh.json missing from en.json "
            f"(stale translation — dead entries):"
        )
        for k in missing_in_en[:5]:
            errors.append(f"    {k!r}")
        if len(missing_in_en) > 5:
            errors.append(f"    ... and {len(missing_in_en) - 5} more")

    # Check 3: empty values
    empty_en = [k for k, v in en.items() if not (isinstance(v, str) and v.strip())]
    empty_zh = [k for k, v in zh.items() if not (isinstance(v, str) and v.strip())]
    if empty_en:
        errors.append(f"{len(empty_en)} empty value(s) in en.json")
    if empty_zh:
        errors.append(f"{len(empty_zh)} empty value(s) in zh.json")

    if errors:
        print("❌ i18n consistency check FAILED:\n")
        for e in errors:
            print(f"  {e}")
        return 1

    print(
        f"✅ i18n consistency OK — en.json={len(en)} keys, zh.json={len(zh)} keys, "
        f"0 over-escaped, 0 missing, 0 stale, 0 empty"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
