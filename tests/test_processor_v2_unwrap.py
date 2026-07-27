"""Tests for processor_v2._unwrap_research_settings and A/B/C path integration.

Covers the bug where path C (notify_research_queued → _start_research_directly)
failed to unwrap the outer research_settings dict before passing it to
start_research_process, causing get_setting_from_snapshot to read nested
keys as missing and fall back to defaults.

Bug: report.enable_images was read as False even when configured True,
because the lookup was being performed on the OUTER dict
({submission, system, settings_snapshot: {...}}) instead of the INNER
snapshot.
"""

import logging

from local_deep_research.web.queue.processor_v2 import QueueProcessorV2


# --- A. Tool unit tests (6) ---


def test_new_structure_normal():
    """New shape: unwrap into (inner_snapshot, submission_dict)."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)  # bypass __init__
    outer = {
        "submission": {"model_provider": "openai", "model": "gpt-4"},
        "system": {"app.queue_mode": "direct"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
            "llm.model": {"value": "gpt-4"},
        },
    }
    snapshot, submission = qp._unwrap_research_settings(outer)
    assert snapshot == {
        "report.enable_images": {"value": True},
        "llm.model": {"value": "gpt-4"},
    }
    assert submission == {
        "model_provider": "openai",
        "model": "gpt-4",
    }


def test_legacy_structure_compatibility():
    """Legacy shape: pass-through, empty submission."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    legacy = {"report.enable_images": {"value": True}}
    snapshot, submission = qp._unwrap_research_settings(legacy)
    assert snapshot == {"report.enable_images": {"value": True}}
    assert submission == {}


def test_none_input():
    """None → empty dicts, no exception."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    snapshot, submission = qp._unwrap_research_settings(None)
    assert snapshot == {}
    assert submission == {}


def test_empty_dict_input():
    """{} → empty dicts, no exception."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    snapshot, submission = qp._unwrap_research_settings({})
    assert snapshot == {}
    assert submission == {}


def test_submission_not_a_dict_warns(caplog):
    """submission present but not dict → warn + empty submission_params."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    weird = {
        "submission": "not-a-dict",
        "settings_snapshot": {"report.enable_images": {"value": True}},
    }
    with caplog.at_level(logging.WARNING):
        snapshot, submission = qp._unwrap_research_settings(weird)
    assert submission == {}
    assert "submission is not a dict" in caplog.text
    # settings_snapshot should still be returned
    assert snapshot == {"report.enable_images": {"value": True}}


def test_key_names_passthrough():
    """Mixed-case / dotted keys are not normalized."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    outer = {
        "submission": {"Mixed_Case_Key": "v"},
        "settings_snapshot": {
            "report.EnableImages": {"value": True},
            "LLM.temperature": {"value": 0.7},
        },
    }
    snapshot, submission = qp._unwrap_research_settings(outer)
    assert "report.EnableImages" in snapshot
    assert "LLM.temperature" in snapshot
    assert submission == {"Mixed_Case_Key": "v"}


# --- B. Path C regression tests (2) — the bug's core ---


def test_path_c_unwrap_inner_snapshot_reaches_start_research_process(monkeypatch):
    """Path C: outer dict → start_research_process receives inner snapshot.

    Without unwrap, start_research_process receives the OUTER dict and
    get_setting_from_snapshot('report.enable_images', False) returns False.
    With unwrap, it receives the INNER snapshot and returns True.
    """
    from local_deep_research.config import thread_settings

    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    # Outer dict as kwargs would deliver it (mimics _queue_research output)
    outer_settings = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    # Simulate what _start_research_directly does AFTER the fix:
    # it calls _unwrap_research_settings on the kwargs settings_snapshot
    # and passes the inner snapshot to start_research_process.
    inner_snapshot, submission_params = qp._unwrap_research_settings(
        outer_settings
    )

    # Now simulate run_research_process looking up the setting.
    enable_images = thread_settings.get_setting_from_snapshot(
        "report.enable_images",
        False,
        settings_snapshot=inner_snapshot,
    )
    assert enable_images is True
    assert submission_params == {"model_provider": "openai"}


def test_path_c_buggy_old_behavior_would_return_false():
    """Demonstrate the bug: passing OUTER dict returns False (default)."""
    from local_deep_research.config import thread_settings

    outer_settings = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    # Without unwrap, passing outer dict directly:
    enable_images = thread_settings.get_setting_from_snapshot(
        "report.enable_images",
        False,
        settings_snapshot=outer_settings,
    )
    # BUG: this is False — the value exists only inside settings_snapshot
    # nested key, which get_setting_from_snapshot does NOT recurse into.
    assert enable_images is False


# --- C. Path A regression test (1) ---


def test_path_a_extraction_equivalent(monkeypatch):
    """Path A: outer dict via _unwrap returns same inner as old single-layer .get().

    research_routes.py:830 old code:
        snapshot_data = research_settings.get('settings_snapshot', {})
    New code uses _unwrap_research_settings. The inner snapshot must match.
    """
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    research_settings = {
        "submission": {"model_provider": "openai"},
        "system": {"app.queue_mode": "direct"},
        "settings_snapshot": {"report.enable_images": {"value": True}},
    }

    # Old single-layer
    old_snapshot = research_settings.get("settings_snapshot", {})

    # New unwrap
    new_snapshot, _ = qp._unwrap_research_settings(research_settings)

    assert new_snapshot == old_snapshot


# --- D. Path B equivalence tests (2) ---


def test_path_b_new_structure_unwrapped():
    """Path B with new-structure QueuedResearch row: inner snapshot delivered."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    # QueuedResearch row would carry this in settings_snapshot
    queued_research_settings_snapshot = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    inner_snapshot, submission_params = qp._unwrap_research_settings(
        queued_research_settings_snapshot
    )

    assert "report.enable_images" in inner_snapshot
    assert inner_snapshot["report.enable_images"] == {"value": True}
    assert submission_params == {"model_provider": "openai"}


def test_path_b_legacy_structure_backward_compat():
    """Path B with legacy-structure QueuedResearch row: passthrough."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    legacy_queued = {"report.enable_images": {"value": True}}

    inner_snapshot, submission_params = qp._unwrap_research_settings(
        legacy_queued
    )

    assert inner_snapshot == {"report.enable_images": {"value": True}}
    assert submission_params == {}
