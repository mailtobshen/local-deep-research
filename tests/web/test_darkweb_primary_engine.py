"""Phase 2+: darkweb as primary engine option.

When the user selects "暗网 (Tor)" from the engine dropdown, the darkweb
engine becomes the primary search source — not an append. The per-
research checkbox is force-disabled because adding the darkweb engine
a second time would be a duplicate search.

The override logic lives in research_routes._apply_darkweb_override.

SECURITY: The "search_engine == darkweb" branch must NOT bypass an
admin-disabled global toggle. Failing closed here means a user
submitting search_engine="darkweb" in their request payload cannot
turn the darkweb engine on when the admin has it off (e.g. ldr-tor
is unreachable or searxng engines-darkweb.yml isn't merged).
"""
from local_deep_research.web.routes.research_routes import (
    _apply_darkweb_override,
)


def _make_snapshot(enabled: bool = False):
    return {
        "settings_snapshot": {
            "search.engine.web.darkweb.enabled": {"value": enabled},
        }
    }


def test_darkweb_as_primary_with_global_on_enables():
    """Admin enabled darkweb AND user picked '暗网 (Tor)' as primary → on."""
    rs = _make_snapshot(enabled=True)
    _apply_darkweb_override(
        rs,
        include_darkweb=False,
        search_engine="darkweb",
    )
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is True


def test_darkweb_as_primary_with_global_off_fails_closed():
    """SECURITY: admin disabled darkweb → user cannot turn it on by
    submitting search_engine="darkweb". The override leaves the
    snapshot unchanged so the research flow's _darkweb_enabled
    check (False) gates the engine out entirely."""
    rs = _make_snapshot(enabled=False)
    _apply_darkweb_override(
        rs,
        include_darkweb=True,  # user ticked the box, but moot
        search_engine="darkweb",
    )
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is False


def test_darkweb_as_primary_with_global_off_no_payload_escape():
    """Even if include_darkweb=True, payload tampering cannot enable
    the darkweb engine when the admin toggle is off."""
    rs = _make_snapshot(enabled=False)
    _apply_darkweb_override(
        rs,
        include_darkweb=True,
        search_engine="darkweb",
    )
    # Snapshot unchanged.
    assert rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]["value"] is False


def test_darkweb_as_primary_no_global_setting_leaves_off():
    """No global entry at all → fail closed (no implicit opt-in)."""
    rs = {"settings_snapshot": {}}
    _apply_darkweb_override(
        rs,
        include_darkweb=False,
        search_engine="darkweb",
    )
    # Should not invent a True entry.
    assert "search.engine.web.darkweb.enabled" not in rs["settings_snapshot"]


def test_non_darkweb_primary_respects_existing_logic():
    """When primary is NOT darkweb, behaviour is unchanged: include_darkweb
    controls the override, the global toggle can still gate."""
    rs = _make_snapshot(enabled=True)
    _apply_darkweb_override(
        rs,
        include_darkweb=True,
        search_engine="searxng",
    )
    existing = rs["settings_snapshot"]["search.engine.web.darkweb.enabled"]
    # Existing entry preserved — admin's true is kept.
    assert existing["value"] is True


def test_non_darkweb_primary_checkbox_off_forces_disabled():
    rs = _make_snapshot(enabled=True)
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