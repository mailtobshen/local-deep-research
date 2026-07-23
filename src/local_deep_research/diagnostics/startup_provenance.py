"""Glue layer that wires the pure provenance collector into application
startup.
"""

from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from local_deep_research.diagnostics import provenance as _provenance


_LOG_ALLOWLIST = (
    "event",
    "captured_at",
    "package_version",
    "python_version",
    "source_sha",
    "source_root",
    "build_sha",
    "image_ref",
    "runtime_mode",
    "data_dir",
)

_FALLBACK_PAYLOAD: dict[str, Any] = {
    "event": "ldr_startup_provenance",
    "error": "collector_failed",
}


def _redact_for_log(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` containing only :data:`_LOG_ALLOWLIST`."""
    return {key: payload[key] for key in _LOG_ALLOWLIST if key in payload}


def emit_startup_provenance(
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Capture, persist, and log startup identity without blocking startup."""
    payload: dict[str, Any]
    try:
        payload = _provenance.collect_provenance(data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup provenance: collector failed: {}", exc)
        return dict(_FALLBACK_PAYLOAD)

    try:
        _provenance.persist_provenance(payload, data_dir=data_dir or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup provenance: persistence failed: {}", exc)

    try:
        logger.info("startup provenance: {}", _redact_for_log(payload))
    except Exception:  # noqa: BLE001 — logging must not crash startup
        pass

    return payload


__all__ = ["emit_startup_provenance"]