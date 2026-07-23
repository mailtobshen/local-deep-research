"""Glue layer that wires the pure provenance collector into application
startup.
"""

from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from local_deep_research.diagnostics import provenance as _provenance


# Allowlist of fields safe to surface in the log record.  Anything else
# in the collector payload — caller-attached secrets, debug scratch —
# is dropped before logging.
#
# Note on ``working_tree_dirty`` (deliberately omitted):
#   The on-disk provenance artifact (``runtime/provenance.json``) is
#   the canonical record and MUST include this field verbatim: it is
#   the primary signal that someone is running a build whose source
#   tree differs from the recorded ``source_sha``, and operators need
#   to see it without re-deriving it.  The startup log line, by
#   contrast, is read by humans tailing logs in real time; printing
#   ``working_tree_dirty=true`` for every dev iteration would drown
#   the more useful single-line summary.  Persist it, do not log it.
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


def _is_writable_data_dir(data_dir: str | Path | None) -> bool:
    """Return True iff ``data_dir`` is a non-empty string/Path.

    The provenance persistence step requires a real target directory —
    passing ``""`` or ``None`` would create ``<cwd>/runtime`` or fail
    with a confusing ``FileNotFoundError`` that the helper would
    then log as a spurious persistence failure. Callers that have no
    configured data directory yet (e.g. unit tests, or a process that
    aborts startup before configuration) should skip persistence
    entirely rather than attempt to write to a sentinel location.
    """
    if data_dir is None:
        return False
    try:
        return bool(str(data_dir))
    except Exception:  # noqa: BLE001
        return False


def emit_startup_provenance(
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Capture, persist, and log startup identity without blocking startup.

    Parameters
    ----------
    data_dir:
        Target directory for the runtime provenance artifact. Forwarded
        to both :func:`collect_provenance` and :func:`persist_provenance`.
        ``None`` or empty string is accepted and means *log only*: the
        helper records no provenance file rather than attempt to write
        to a sentinel path that would log a spurious persistence
        failure.

    Returns
    -------
    dict[str, Any]
        The collector payload (or a minimal fallback dict if the
        collector itself crashed). Persistence and logging errors are
        swallowed; the function never raises so callers can invoke it
        from the safest possible point in the startup sequence.
    """
    payload: dict[str, Any]
    try:
        payload = _provenance.collect_provenance(data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001 — startup must never abort here
        logger.warning("startup provenance: collector failed: {}", exc)
        return dict(_FALLBACK_PAYLOAD)

    if _is_writable_data_dir(data_dir):
        try:
            _provenance.persist_provenance(
                payload, data_dir=data_dir  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("startup provenance: persistence failed: {}", exc)

    try:
        logger.info("startup provenance: {}", _redact_for_log(payload))
    except Exception:  # noqa: BLE001 — logging must not crash startup
        pass

    return payload


__all__ = ["emit_startup_provenance"]


# Self-test entry point.  Run with:
#     PYTHONPATH=src python -m local_deep_research.diagnostics.startup_provenance
if __name__ == "__main__":  # pragma: no cover — manual smoke only
    import json as _json

    _out = emit_startup_provenance(data_dir=None)
    print(_json.dumps(_out, indent=2, sort_keys=True))
