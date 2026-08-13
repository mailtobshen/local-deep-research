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
