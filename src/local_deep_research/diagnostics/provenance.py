"""Pure runtime provenance collector.

Captures a small, redacted JSON-serializable identity record for the running
LDR process. Designed to be invoked at startup with explicit environment and
data-directory inputs so the same code path is used in production and tests.

The module is pure:

* Reads only an allowlisted subset of the environment
  (``LDR_BUILD_SHA``, ``LDR_IMAGE_REF``, ``LDR_RUNTIME_MODE``).
* Resolves the imported ``local_deep_research`` package path, falling back to
  a caller-supplied ``package_file`` (used by tests).
* Best-effort inspects the source root for a ``.git`` directory and a commit
  via ``git``; any failure or non-zero exit yields :data:`UNKNOWN`.
* Does not log raw environment contents or any other secrets.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess  # noqa: S404 - bounded allowlisted args, no shell interpolation
import tempfile
from pathlib import Path
from typing import Any, Mapping

UNKNOWN = "unknown"

_EVENT = "ldr_startup_provenance"
_ENV_KEYS = {
    "build_sha": "LDR_BUILD_SHA",
    "image_ref": "LDR_IMAGE_REF",
    "runtime_mode": "LDR_RUNTIME_MODE",
}
_DEFAULT_PACKAGE_NAME = "local_deep_research"
_GIT_TIMEOUT_SECONDS = 2


def _resolve_module_file(
    package_name: str, package_file: str | Path | None
) -> Path | None:
    """Return the resolved package ``__init__`` path, or ``None`` if unknown."""
    if package_file is not None:
        candidate = Path(package_file)
        if candidate.exists():
            return candidate.resolve()
        return None
    try:
        importlib.import_module(package_name)
    except Exception:
        return None
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve()


def _derive_source_root(module_file: Path | None) -> str:
    """Best-effort source root derived from the package ``__init__`` path.

    Returns the parent of the package directory (i.e. two parents up from
    ``__init__.py``). Falls back to :data:`UNKNOWN` when the package path
    cannot be resolved.
    """
    if module_file is None:
        return UNKNOWN
    try:
        return str(module_file.resolve().parent.parent)
    except OSError:
        return UNKNOWN


def _run_git(source_root: Path, args: list[str]) -> str:
    """Run a bounded ``git`` invocation with the standard safety net.

    * Argv list (no shell interpolation).
    * ``cwd`` set to the candidate source root.
    * ``timeout`` of a few seconds.
    * ``check=False`` so non-zero exit propagates to the caller.
    """
    result = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=source_root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} exited {result.returncode}")
    return result.stdout.strip()


def _git_identity(source_root: str) -> tuple[str, str]:
    """Return ``(source_sha, working_tree_dirty)`` for ``source_root``.

    Both fields default to :data:`UNKNOWN` on any failure (no ``.git``,
    missing ``git`` binary, timeout, non-zero exit).
    """
    root = Path(source_root)
    if not (root / ".git").exists():
        return UNKNOWN, UNKNOWN

    try:
        sha = _run_git(root, ["rev-parse", "HEAD"])
    except (
        OSError,
        subprocess.TimeoutExpired,
        RuntimeError,
        UnicodeDecodeError,
    ):
        return UNKNOWN, UNKNOWN

    try:
        status_output = _run_git(root, ["status", "--porcelain"])
    except (
        OSError,
        subprocess.TimeoutExpired,
        RuntimeError,
        UnicodeDecodeError,
    ):
        return sha, UNKNOWN

    return sha, "true" if status_output else "false"


def _safe_env_value(environ: Mapping[str, str], key: str) -> str:
    """Return the environment value when present and non-blank, else UNKNOWN."""
    value = environ.get(key)
    if value is None or not str(value).strip():
        return UNKNOWN
    return str(value).strip()


def _package_version(package_name: str) -> str:
    """Resolve the installed package version, falling back to UNKNOWN."""
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        from local_deep_research import __version__
    except Exception:
        return UNKNOWN
    return getattr(__version__, "__version__", UNKNOWN)


def collect_provenance(
    *,
    package_name: str = _DEFAULT_PACKAGE_NAME,
    package_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    data_dir: str | Path | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Collect a JSON-safe provenance snapshot for the running process.

    Returns a dictionary of JSON-safe primitives only (``str`` / ``bool`` /
    JSON numbers). Missing or blank optional values are normalized to
    :data:`UNKNOWN`; the function never raises for missing Git or
    environment metadata.
    """
    env = environ if environ is not None else os.environ
    module_file = _resolve_module_file(package_name, package_file)
    source_root = _derive_source_root(module_file)
    source_sha, working_tree_dirty = _git_identity(source_root)

    module_file_str = str(module_file) if module_file is not None else UNKNOWN
    data_dir_str = str(data_dir) if data_dir is not None else UNKNOWN

    return {
        "event": _EVENT,
        "captured_at": captured_at or UNKNOWN,
        "package_version": _package_version(package_name),
        "python_version": platform.python_version(),
        "module_file": module_file_str,
        "source_root": source_root,
        "source_sha": source_sha,
        "working_tree_dirty": working_tree_dirty,
        "runtime_mode": _safe_env_value(env, _ENV_KEYS["runtime_mode"]),
        "build_sha": _safe_env_value(env, _ENV_KEYS["build_sha"]),
        "image_ref": _safe_env_value(env, _ENV_KEYS["image_ref"]),
        "data_dir": data_dir_str,
    }


def persist_provenance(
    provenance: Mapping[str, Any], *, data_dir: str | Path
) -> Path:
    """Atomically write ``provenance`` to ``<data_dir>/runtime/provenance.json``.

    Creates ``<data_dir>/runtime`` if missing, writes a sibling ``.tmp`` file
    so concurrent readers never observe a half-written file, then uses
    :func:`os.replace` for an atomic rename. Raises whatever the underlying
    filesystem call raises so the startup boundary can catch it.
    """
    runtime_dir = Path(data_dir) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    final_path = runtime_dir / "provenance.json"

    payload = json.dumps(dict(provenance), ensure_ascii=False, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=runtime_dir,
        prefix=".provenance.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_name = handle.name

    os.replace(tmp_name, final_path)
    # Best-effort cleanup of any stray sibling temp files left by earlier
    # crashed writes; never raises.
    for stray in runtime_dir.glob(".provenance.*.tmp"):
        try:
            stray.unlink()
        except OSError:
            pass

    return final_path


__all__ = [
    "UNKNOWN",
    "collect_provenance",
    "persist_provenance",
]
