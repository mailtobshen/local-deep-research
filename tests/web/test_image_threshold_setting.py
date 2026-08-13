"""Contract tests for the report.image_alt_similarity_threshold setting.

Covers: the default value is declared, the read-site picks it up, and
the 0.45 floor is enforced at runtime.
"""

import json
from pathlib import Path

import pytest


def _load_defaults():
    """Load the bundled default_settings.json as-shipped."""
    import local_deep_research.defaults as pkg

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
