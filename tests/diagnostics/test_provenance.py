"""Tests for the pure runtime provenance collector."""

from __future__ import annotations

import json
from pathlib import Path

from local_deep_research.diagnostics.provenance import (
    UNKNOWN,
    collect_provenance,
    persist_provenance,
)


def test_collect_provenance_returns_json_safe_contract(tmp_path: Path) -> None:
    package_file = (
        tmp_path / "site-packages" / "local_deep_research" / "__init__.py"
    )
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    result = collect_provenance(
        package_file=package_file,
        environ={
            "LDR_BUILD_SHA": "build-123",
            "LDR_IMAGE_REF": "ldr-local:build-123",
            "LDR_RUNTIME_MODE": "hot_mount",
        },
        data_dir=tmp_path / "data",
        captured_at="2026-07-23T00:00:00+00:00",
    )

    assert result["event"] == "ldr_startup_provenance"
    assert result["module_file"] == str(package_file.resolve())
    assert result["build_sha"] == "build-123"
    assert result["image_ref"] == "ldr-local:build-123"
    assert result["runtime_mode"] == "hot_mount"
    json.dumps(result)


def test_missing_optional_metadata_is_explicitly_unknown(
    tmp_path: Path,
) -> None:
    package_file = tmp_path / "local_deep_research" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    result = collect_provenance(
        package_file=package_file,
        environ={},
        data_dir=tmp_path / "data",
        captured_at="2026-07-23T00:00:00+00:00",
    )

    assert result["build_sha"] == UNKNOWN
    assert result["image_ref"] == UNKNOWN
    assert result["runtime_mode"] == UNKNOWN
    assert result["source_sha"] == UNKNOWN
    assert result["working_tree_dirty"] == UNKNOWN


def test_persist_provenance_writes_runtime_json_atomically(
    tmp_path: Path,
) -> None:
    payload = {"event": "ldr_startup_provenance", "source_sha": "abc"}

    path = persist_provenance(payload, data_dir=tmp_path)

    assert path == tmp_path / "runtime" / "provenance.json"
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not list(path.parent.glob("*.tmp"))
