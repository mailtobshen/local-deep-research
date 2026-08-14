"""Task 6: search.engine.web.darkweb.* settings + zh.json translations."""
import json
from pathlib import Path

import local_deep_research.defaults as defaults_pkg
import local_deep_research.web.translations as tr_pkg


def _load_defaults():
    path = Path(defaults_pkg.__file__).parent / "default_settings.json"
    return json.loads(path.read_text())


def _load_zh():
    path = Path(tr_pkg.__file__).parent / "zh.json"
    return json.loads(path.read_text())


def test_darkweb_enabled_declared():
    d = _load_defaults()
    key = "search.engine.web.darkweb.enabled"
    assert key in d
    entry = d[key]
    assert entry["value"] is False
    assert entry["ui_element"] == "checkbox"
    assert entry["editable"] is True


def test_darkweb_display_name_declared():
    d = _load_defaults()
    key = "search.engine.web.darkweb.display_name"
    assert key in d
    assert d[key]["ui_element"] == "text"


def test_darkweb_reliability_declared():
    d = _load_defaults()
    key = "search.engine.web.darkweb.reliability"
    assert key in d
    assert d[key]["ui_element"] == "number"
    assert d[key]["value"] == 0.3


def test_darkweb_default_params_declared():
    d = _load_defaults()
    params_key = "search.engine.web.darkweb.default_params"
    assert params_key in d
    val = d[params_key]["value"]
    assert val["engines"] == ["ahmia", "torch"]
    assert val["categories"] == ["onions"]
    assert val["max_results"] == 10


def test_zh_translation_present_for_darkweb():
    d = _load_defaults()
    zh = _load_zh()
    name_en = d["search.engine.web.darkweb.display_name"]["name"]
    desc_en = d["search.engine.web.darkweb.display_name"]["description"]
    # Either name or description key must be in zh.json (any of them is enough).
    assert name_en in zh or desc_en in zh, (
        f"zh.json must translate one of: {name_en!r} / {desc_en!r}"
    )