"""Phase 2+: darkweb as primary engine option.

When the user selects "暗网 (Tor)" from the engine dropdown, the darkweb
engine becomes the primary search source — not an append. The per-
research checkbox is force-disabled because adding the darkweb engine
a second time would be a duplicate search.

The override logic lives in research_routes._apply_darkweb_override.
"""
from local_deep_research.web.routes.research_routes import (
    _apply_darkweb_override,
)


def _make_snapshot():
    return {
        "settings_snapshot": {
            "search.engine.web.darkweb.enabled": {"value": False},
        }
    }


def test_darkweb_as_primary_enables_darkweb():
    """Selecting '暗网 (Tor)' as the primary engine forces enabled=True."""
    rs = _make_snapshot()
    _apply_darkweb_override(
        rs,
        include_darkweb=False,
        search_engine="darkweb",
    )
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is True


def test_darkweb_as_primary_forces_checkbox_off():
    """When darkweb is the primary engine, the append-checkbox is moot;
    we record include_darkweb=False so the merge branch is a no-op."""
    rs = _make_snapshot()
    _apply_darkweb_override(
        rs,
        include_darkweb=True,  # user ticked the box, but moot
        search_engine="darkweb",
    )
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is True
    # The merge side reads include_darkweb via the call site (route passes
    # include_darkweb=data.get('include_darkweb')), so this just makes
    # sure the override doesn't muck with the global flag.


def test_non_darkweb_primary_respects_existing_logic():
    """When primary is NOT darkweb, behaviour is unchanged: include_darkweb
    controls the override, the global toggle can still gate."""
    rs = _make_snapshot()
    _apply_darkweb_override(
        rs,
        include_darkweb=True,
        search_engine="searxng",
    )
    existing = rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]
    # Existing entry untouched (global toggle wins).
    assert existing["value"] is False  # was False in the snapshot


def test_non_darkweb_primary_checkbox_off_forces_disabled():
    rs = _make_snapshot()
    _apply_darkweb_override(
        rs,
        include_darkweb=False,
        search_engine="arxiv",
    )
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is False


def test_no_snapshot_is_noop():
    rs = {}
    _apply_darkweb_override(
        rs,
        include_darkweb=True,
        search_engine="darkweb",
    )
    assert rs == {}