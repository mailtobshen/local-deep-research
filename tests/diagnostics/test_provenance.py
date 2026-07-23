"""Tests for the pure runtime provenance collector."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import pytest

from local_deep_research.diagnostics import provenance as provenance_module
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


def test_package_version_falls_back_to_module_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``importlib.metadata.version()`` cannot find the package, the
    collector should still surface the version string from
    ``local_deep_research.__version__`` rather than reporting ``unknown``.

    Regression: the previous fallback used ``getattr(__version__, "__version__",
    UNKNOWN)`` against the already-resolved module attribute (which is itself
    the version string), so dev / editable installs always reported ``unknown``.
    """

    def _raise_package_not_found(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError("local_deep_research")

    monkeypatch.setattr(
        importlib.metadata,
        "version",
        _raise_package_not_found,
    )

    result = provenance_module._package_version("local_deep_research")

    assert result == "1.6.12"
    assert result != provenance_module.UNKNOWN


def test_derive_source_root_walks_up_to_dot_git(tmp_path: Path) -> None:
    """When ``.git`` lives above the package directory (typical checkout
    layout), ``source_root`` should climb up to the directory that
    contains it rather than reporting a path that lacks git identity.
    """
    git_root = tmp_path / "work"
    package_dir = git_root / "src" / "local_deep_research"
    package_file = package_dir / "__init__.py"
    package_dir.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")
    (git_root / ".git").mkdir()

    result = provenance_module._derive_source_root(package_file)

    assert result == str(git_root)


def test_derive_source_root_falls_back_without_dot_git(tmp_path: Path) -> None:
    """When no ``.git`` ancestor is reachable (installed container
    layout), ``source_root`` should still report the package's parent
    so callers see a real path; git identity will report ``unknown``.
    """
    package_dir = tmp_path / "site-packages" / "local_deep_research"
    package_file = package_dir / "__init__.py"
    package_dir.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    result = provenance_module._derive_source_root(package_file)

    assert result == str(package_dir.parent)


def test_persist_provenance_is_idempotent_under_overwrite(
    tmp_path: Path,
) -> None:
    """Re-writing the same payload should replace the prior file in
    place (no ``.tmp`` leftover, no second generation ``.json.bak``)
    and the latest payload wins.
    """
    first = {"event": "ldr_startup_provenance", "source_sha": "abc"}
    second = {"event": "ldr_startup_provenance", "source_sha": "xyz"}

    path = persist_provenance(first, data_dir=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == first

    persist_provenance(second, data_dir=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == second
    assert not list(path.parent.glob("*.tmp"))
    assert path == tmp_path / "runtime" / "provenance.json"
