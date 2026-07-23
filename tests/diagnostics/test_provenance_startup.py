"""Startup integration tests for the runtime provenance hook.

Exercises :func:`emit_startup_provenance` — the glue layer that
:func:`local_deep_research.web.app_factory.create_app` calls once per
process to capture, persist, and log a redacted identity snapshot.

Contract:

* The helper must invoke :func:`collect_provenance` with the configured
  data directory and return the resulting payload.
* Persistence is best-effort: an :class:`OSError` from
  :func:`persist_provenance` must NOT propagate to the caller.
* The structured log event must contain the collector's payload
  (allowlisted fields only) and must NEVER carry a secret / env value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from local_deep_research.diagnostics import startup_provenance


@pytest.fixture(autouse=True)
def _enable_ldr_logging() -> None:
    """Re-enable loguru for ``local_deep_research.*`` modules.

    The package's ``__init__`` calls :func:`loguru.logger.disable` on
    import. Tests for the startup helper want to observe what the
    helper actually sends to loguru, so flip the disabled namespace
    back on for the duration of each test.
    """
    logger.enable("local_deep_research")
    yield
    # Leave the logger in whatever state the next test wants; the
    # fixture's ``enable`` call is idempotent.


class _CollectRaises(RuntimeError):
    """Sentinel to prove collector failures do not abort startup."""


def test_startup_provenance_failure_does_not_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Persistence failure must not abort startup."""
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.collect_provenance",
        lambda **kwargs: {"event": "ldr_startup_provenance", "source_sha": "abc"},
    )
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.persist_provenance",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )

    result = startup_provenance.emit_startup_provenance(
        data_dir=tmp_path / "data",
    )

    assert result["source_sha"] == "abc"


def test_collector_failure_does_not_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A collector crash must surface as a minimal fallback, not a raise."""
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.collect_provenance",
        lambda **kwargs: (_ for _ in ()).throw(_CollectRaises("boom")),
    )

    result = startup_provenance.emit_startup_provenance(
        data_dir=tmp_path / "data",
    )

    # Never raised; result is a fallback dict the caller can inspect.
    assert isinstance(result, dict)
    assert result["event"] == "ldr_startup_provenance"


def test_startup_provenance_persists_and_logs_redacted_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Happy path: collector + persistence + redacted log line."""
    payload: dict[str, Any] = {
        "event": "ldr_startup_provenance",
        "captured_at": "2026-07-23T00:00:00+00:00",
        "package_version": "1.6.12",
        "python_version": "3.12.3",
        "module_file": "/srv/ldr/__init__.py",
        "source_root": "/srv/ldr",
        "source_sha": "abcdef",
        "working_tree_dirty": "false",
        "runtime_mode": "hot_mount",
        "build_sha": "build-123",
        "image_ref": "ldr-local:build-123",
        "data_dir": str(tmp_path / "data"),
        "secret_value": "should-not-be-logged",
        "api_key": "should-not-be-logged",
    }

    captured: dict[str, Any] = {}

    def _collect(**_kwargs: Any) -> dict[str, Any]:
        return dict(payload)

    def _persist(provenance: dict[str, Any], *, data_dir: Path) -> Path:
        captured["persisted_payload"] = dict(provenance)
        captured["persisted_path"] = data_dir / "runtime" / "provenance.json"
        captured["persisted_path"].parent.mkdir(parents=True, exist_ok=True)
        captured["persisted_path"].write_text("{}", encoding="utf-8")
        return captured["persisted_path"]

    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.collect_provenance",
        _collect,
    )
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.persist_provenance",
        _persist,
    )

    result = startup_provenance.emit_startup_provenance(
        data_dir=tmp_path / "data",
    )

    # Return value matches collector output.
    assert result["source_sha"] == "abcdef"
    assert result["build_sha"] == "build-123"

    # Persistence received the full redacted collector payload
    # (secret fields are not stripped — the collector's contract is
    # already allowlisted, but the startup helper must not amplify
    # exposure).  What matters is that the redacted log line does NOT
    # carry secret fields.  See :func:`test_log_line_is_allowlisted`.
    assert "secret_value" in captured["persisted_payload"]


def test_log_line_is_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The logger must receive only the allowlisted fields, never secrets.

    Loguru ships its own handler stack; ``caplog`` cannot intercept it.
    Install a transient string sink and assert on what the helper
    actually sends to loguru.
    """
    payload: dict[str, Any] = {
        "event": "ldr_startup_provenance",
        "captured_at": "2026-07-23T00:00:00+00:00",
        "package_version": "1.6.12",
        "python_version": "3.12.3",
        "source_sha": "abcdef",
        "source_root": "/srv/ldr",
        "build_sha": "build-123",
        "image_ref": "ldr-local:build-123",
        "runtime_mode": "hot_mount",
        "data_dir": str(tmp_path / "data"),
        "SECRET_API_KEY": "deadbeef",
        "OPENAI_TOKEN": "cafef00d",
    }

    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.collect_provenance",
        lambda **_: dict(payload),
    )
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.persist_provenance",
        lambda provenance, *, data_dir: data_dir
        / "runtime"
        / "provenance.json",
    )

    captured: list[str] = []

    sink_id = logger.add(
        lambda message: captured.append(message),
        level="INFO",
        format="{message}",
    )
    try:
        startup_provenance.emit_startup_provenance(
            data_dir=tmp_path / "data",
        )
    finally:
        logger.remove(sink_id)

    rendered = "\n".join(captured)

    # Allowlisted summary is present.
    assert "ldr_startup_provenance" in rendered
    assert "abcdef" in rendered  # source_sha is safe to log
    assert "1.6.12" in rendered  # package_version is safe

    # Secret-bearing fields must NEVER appear in the log.
    assert "SECRET_API_KEY" not in rendered
    assert "OPENAI_TOKEN" not in rendered
    assert "deadbeef" not in rendered
    assert "cafef00d" not in rendered