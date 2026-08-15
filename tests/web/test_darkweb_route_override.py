"""_apply_darkweb_override forces search.engine.web.darkweb.enabled in the
per-research settings_snapshot based on the include_darkweb checkbox state.
"""
from local_deep_research.web.routes.research_routes import (
    _apply_darkweb_override,
)


def test_include_false_forces_disabled():
    rs = {
        "settings_snapshot": {
            "search.engine.web.darkweb.enabled": {"value": True},
        }
    }
    _apply_darkweb_override(rs, include_darkweb=False)
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is False


def test_include_true_preserves_existing_true():
    rs = {
        "settings_snapshot": {
            "search.engine.web.darkweb.enabled": {"value": True},
        }
    }
    _apply_darkweb_override(rs, include_darkweb=True)
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is True


def test_include_true_does_not_enable_global_off():
    """If the global toggle is off, the per-research True cannot enable it."""
    rs = {
        "settings_snapshot": {
            "search.engine.web.darkweb.enabled": {"value": False},
        }
    }
    _apply_darkweb_override(rs, include_darkweb=True)
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is False


def test_no_snapshot_safe():
    """Missing settings_snapshot key is a no-op."""
    rs = {}
    _apply_darkweb_override(rs, include_darkweb=True)
    assert rs == {}


def test_empty_snapshot_safe():
    rs = {"settings_snapshot": None}
    _apply_darkweb_override(rs, include_darkweb=True)
    assert rs["settings_snapshot"] is None