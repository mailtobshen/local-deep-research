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
from datetime import datetime, UTC
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


_SOURCE_ROOT_MAX_ASCENT = 6


def _derive_source_root(module_file: Path | None) -> str:
    """Best-effort source root derived from the package ``__init__`` path.

    Returns the parent of the package directory (i.e. two parents up from
    ``__init__.py``). When that directory does not contain ``.git`` —
    e.g. inside an installed container image where ``.git`` is not
    shipped — walks up a bounded number of parents to locate the
    nearest enclosing ``.git``. Falls back to :data:`UNKNOWN` when the
    package path cannot be resolved.
    """
    if module_file is None:
        return UNKNOWN
    try:
        resolved = module_file.resolve()
    except OSError:
        return UNKNOWN
    candidate = resolved.parent.parent
    for _ in range(_SOURCE_ROOT_MAX_ASCENT + 1):
        if (candidate / ".git").exists():
            return str(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    # No ``.git`` ancestor found within the bound; still surface the
    # package's own parent so callers see a real path rather than
    # ``UNKNOWN``. Git identity will report ``unknown`` separately.
    return str(resolved.parent.parent)


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


def _git_identity_from_dir(git_dir: Path) -> tuple[str, str]:
    """Read ``source_sha`` directly from ``git_dir`` without invoking ``git``.

    Used when the running container has no ``git`` binary but has a bind-mounted
    ``.git`` directory (e.g. compose hot-mount of host source). We read
    ``HEAD`` (which may be a symbolic ref or a raw SHA) and resolve it against
    ``packed-refs`` when needed. ``working_tree_dirty`` is reported as
    :data:`UNKNOWN` because we have no working tree to inspect from inside
    the container.
    """
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return UNKNOWN, UNKNOWN
    try:
        head_value = head_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return UNKNOWN, UNKNOWN

    sha = UNKNOWN
    if head_value.startswith("ref:"):
        ref_path = head_value.split(":", 1)[1].strip()
        ref_file = git_dir / ref_path
        if ref_file.is_file():
            try:
                sha = ref_file.read_text(encoding="utf-8").strip() or UNKNOWN
            except (OSError, UnicodeDecodeError):
                sha = UNKNOWN
        else:
            packed_refs = git_dir / "packed-refs"
            if packed_refs.is_file():
                try:
                    text = packed_refs.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return sha or UNKNOWN, UNKNOWN
                prefix = ref_path + " "
                for line in text.splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    if line.startswith(prefix):
                        sha = line[len(prefix):].strip()
                        break
    elif head_value:
        sha = head_value

    if sha and not all(c in "0123456789abcdef" for c in sha.lower()):
        sha = UNKNOWN

    return sha or UNKNOWN, UNKNOWN


def _git_identity_from_environ(environ: Mapping[str, str]) -> tuple[str, str] | None:
    """If ``LDR_GIT_DIR`` points at a directory, return its identity, else None."""
    raw = environ.get("LDR_GIT_DIR")
    if not raw:
        return None
    path = Path(str(raw).strip())
    if not path.is_dir():
        return None
    return _git_identity_from_dir(path)


def _safe_env_value(environ: Mapping[str, str], key: str) -> str:
    """Return the environment value when present and non-blank, else UNKNOWN."""
    value = environ.get(key)
    if value is None or not str(value).strip():
        return UNKNOWN
    return str(value).strip()


def _package_version(package_name: str) -> str:
    """Resolve the installed package version, falling back to UNKNOWN.

    Prefers ``importlib.metadata.version()`` (works for installed wheels
    and PEP 660 editable installs) and falls back to the version string
    already exported by the ``local_deep_research`` package itself,
    which is what editable / source-checkout runs see.
    """
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        from local_deep_research import __version__ as module_version
    except Exception:
        return UNKNOWN
    # ``from local_deep_research import __version__`` binds either the
    # submodule (``local_deep_research.__version__``) or — when the
    # submodule is a plain string — the string itself. Both cases
    # already carry the version we want; ``getattr(module, "__version__")``
    # against the resolved string was the previous bug that masked the
    # version in editable / source-checkout runs.
    if isinstance(module_version, str) and module_version.strip():
        return module_version.strip()
    if hasattr(module_version, "__version__"):
        candidate = getattr(module_version, "__version__", UNKNOWN)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return UNKNOWN


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

    # Prefer an explicit ``LDR_GIT_DIR`` override so containers without a
    # ``git`` binary can still report their source identity from a
    # bind-mounted ``.git`` directory.
    git_from_environ = _git_identity_from_environ(env)
    if git_from_environ is not None:
        source_sha, working_tree_dirty = git_from_environ
    else:
        source_sha, working_tree_dirty = _git_identity(source_root)

    module_file_str = str(module_file) if module_file is not None else UNKNOWN
    data_dir_str = str(data_dir) if data_dir is not None else UNKNOWN

    return {
        "event": _EVENT,
        "captured_at": captured_at or datetime.now(UTC).isoformat(),
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

    # Durability: fsync the directory entry so the rename is guaranteed
    # visible after a crash. Best-effort: some filesystems reject the
    # call and it is not strictly required for correctness.
    try:
        dir_fd = os.open(str(runtime_dir), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass

    return final_path


__all__ = [
    "UNKNOWN",
    "collect_provenance",
    "persist_provenance",
]
