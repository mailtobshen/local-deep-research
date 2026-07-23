# Runtime Provenance Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add non-blocking, redacted runtime provenance so the LDR process records which source, package, Python runtime, and deployment metadata generated its logs without changing the existing container or image workflow.

**Architecture:** Add a small pure provenance collector under `src/local_deep_research/diagnostics/` that accepts injectable environment/path/time inputs, resolves the imported package path and best-effort Git identity, and returns JSON-safe data. Add a startup integration at the application entrypoint that persists the record atomically under the configured data directory and logs a concise summary; persistence or metadata failures never stop startup. Keep health and report/image behavior unchanged in this phase.

**Tech Stack:** Python 3.14, Flask/Loguru, pathlib, JSON, pytest, existing `config.paths.get_data_directory()` and application startup entrypoint.

## Global Constraints

- Do not change Docker Compose mounts, image tags, volumes, auxiliary services, or restart behavior.
- Do not change report/image-selection logic or image persistence behavior.
- Unknown metadata must be represented as `"unknown"` and must not block startup.
- Never log API keys, passwords, proxy credentials, or the full environment.
- Persist only under `/data/runtime/provenance.json` or the configured data directory equivalent.
- Persistence and serialization failures must be logged and must not abort application startup.

---

## File Map

- Create: `src/local_deep_research/diagnostics/provenance.py` — pure collection, normalization, and atomic persistence helpers.
- Modify: the existing application startup entrypoint identified before implementation — call provenance once after logging is available and before serving requests; do not put collection in a request handler.
- Test: `tests/diagnostics/test_provenance.py` — collector, fallback, redaction, and persistence contract.
- Modify only if needed: existing startup test module — assert startup integration is non-fatal and emits the event without coupling tests to absolute host paths.
- Do not modify: `docker-compose.ldr-local.yml`, image modules, report models, or health semantics in Phase 1.

## Interfaces

The implementation must provide these stable interfaces:

```python
from pathlib import Path
from typing import Any, Mapping

UNKNOWN = "unknown"


def collect_provenance(
    *,
    package_name: str = "local_deep_research",
    package_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    data_dir: str | Path | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]: ...


def persist_provenance(
    provenance: Mapping[str, Any],
    *,
    data_dir: str | Path,
) -> Path: ...
```

`collect_provenance()` returns JSON-safe primitives and never raises for missing Git or optional environment metadata. `persist_provenance()` writes `/runtime/provenance.json` beneath `data_dir` via a temporary file in the same directory followed by `os.replace`, and raises only for the caller to catch at the startup boundary so tests can verify non-fatal handling there.

### Task 1: Add the pure provenance collector

**Files:**
- Create: `src/local_deep_research/diagnostics/provenance.py`
- Modify: `src/local_deep_research/diagnostics/__init__.py` only if package exports are required by existing conventions
- Test: `tests/diagnostics/test_provenance.py`

**Interfaces:**
- Produces `UNKNOWN`, `collect_provenance()`, `persist_provenance()` exactly as defined above.
- Uses `importlib.metadata.version()` or the existing `__version__` module for `package_version`, with `UNKNOWN` fallback.
- Uses `platform.python_version()` for `python_version`.
- Resolves `module_file` from the supplied `package_file`, or imports the package only when no test override is supplied.
- Derives `source_root` from the resolved package path without assuming `.git` exists inside the container.
- Reads only these optional environment keys: `LDR_BUILD_SHA`, `LDR_IMAGE_REF`, `LDR_RUNTIME_MODE`.

- [ ] **Step 1: Write failing tests for complete and missing metadata**

```python
from pathlib import Path
import json

from local_deep_research.diagnostics.provenance import (
    UNKNOWN,
    collect_provenance,
    persist_provenance,
)


def test_collect_provenance_returns_json_safe_contract(tmp_path):
    package_file = tmp_path / "site-packages" / "local_deep_research" / "__init__.py"
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


def test_missing_optional_metadata_is_explicitly_unknown(tmp_path):
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


def test_persist_provenance_writes_runtime_json_atomically(tmp_path):
    payload = {"event": "ldr_startup_provenance", "source_sha": "abc"}

    path = persist_provenance(payload, data_dir=tmp_path)

    assert path == tmp_path / "runtime" / "provenance.json"
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not list(path.parent.glob("*.tmp"))
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest -q tests/diagnostics/test_provenance.py`.
Expected: collection/import or missing-function failures because the module and interfaces do not yet exist.

- [ ] **Step 3: Write the minimal collector and atomic persistence implementation**

Implement the exact interfaces above. Copy only allowlisted environment keys, normalize missing/blank values to `UNKNOWN`, resolve paths, best-effort inspect parents for `.git`, and return only JSON-safe primitives. `persist_provenance()` creates `data_dir/runtime`, writes a sibling temporary file, flushes/closes it, then calls `os.replace(temp, final)`.

Do not invoke a shell command with interpolated metadata. Git identity lookup must be safe and optional; if implemented with subprocess, pass an argument list and a bounded timeout, and catch `OSError`, timeout, and non-zero exit.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `pytest -q tests/diagnostics/test_provenance.py`.
Expected: all provenance tests pass.

- [ ] **Step 5: Commit the pure collector**

```bash
git add src/local_deep_research/diagnostics/provenance.py src/local_deep_research/diagnostics/__init__.py tests/diagnostics/test_provenance.py
git commit -m "feat: add runtime provenance collector"
```

### Task 2: Integrate provenance at application startup

**Files:**
- Modify: the actual application entrypoint discovered from `Dockerfile`/entrypoint inspection, likely `scripts/ldr_entrypoint.sh` and/or the Python startup module; choose the smallest existing startup hook that runs once per process.
- Test: `tests/diagnostics/test_provenance_startup.py` or the existing startup test file.

**Interfaces:**
- Consumes `collect_provenance()` and `persist_provenance()` from Task 1.
- Produces one startup log event and a best-effort `/data/runtime/provenance.json`.

- [ ] **Step 1: Write a failing startup integration test**

Patch the collector and persistence helpers, invoke the startup hook, and assert:

```python
def test_startup_provenance_failure_does_not_abort(monkeypatch):
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.collect_provenance",
        lambda **kwargs: {"event": "ldr_startup_provenance", "source_sha": "abc"},
    )
    monkeypatch.setattr(
        "local_deep_research.diagnostics.provenance.persist_provenance",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )

    result = emit_startup_provenance()

    assert result["source_sha"] == "abc"
```

Also assert the logger receives only a redacted summary and no environment secret.

- [ ] **Step 2: Run the integration test and verify it fails**

Run: `pytest -q tests/diagnostics/test_provenance_startup.py`.
Expected: failure because the startup helper does not yet exist or is not wired.

- [ ] **Step 3: Add the startup hook**

Implement:

```python
def emit_startup_provenance() -> dict[str, Any]:
    """Capture, persist, and log startup identity without blocking startup."""
```

Call the collector with the configured data directory, persist best-effort, log only an allowlisted summary, catch collector/serialization/filesystem failures, and invoke once during normal startup after logging is configured and before serving requests. Do not add expensive Git/filesystem work to `/health` in this phase.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `pytest -q tests/diagnostics/test_provenance.py tests/diagnostics/test_provenance_startup.py`.
Expected: all focused tests pass.

- [ ] **Step 5: Commit startup integration**

```bash
git add <actual-startup-files> tests/diagnostics/test_provenance_startup.py
git commit -m "feat: record runtime provenance at startup"
```

### Task 3: Run regression verification

**Files:** No new production files unless a focused test exposes a required compatibility fix.

- [ ] **Step 1:** `pytest -q tests/diagnostics tests/health_check tests/web/test_vision_blueprint_registered.py`; expected all selected tests pass.
- [ ] **Step 2:** `ruff check src/local_deep_research/diagnostics tests/diagnostics <actual-startup-files>`; expected no new lint errors.
- [ ] **Step 3:** Run `python -c 'from local_deep_research.diagnostics.provenance import collect_provenance; import json; print(json.dumps(collect_provenance(), ensure_ascii=False))'`; expected valid JSON with explicit unknowns and no secrets.
- [ ] **Step 4:** Run `git diff --check HEAD~2..HEAD`, `git status --short --branch`, and `git log --oneline -3`; expected only approved provenance files changed and a clean worktree.
- [ ] **Step 5:** Commit the approved spec/plan documents if not already committed.

## Verification and stopping point

Phase 1 is complete only when focused tests pass, provenance JSON is valid, startup persistence failure is non-fatal, and the final diff does not alter Docker/Compose or image/report behavior. Do not proceed to immutable images, source/image compatibility gates, report metadata, or image fallback until a separate approved design/plan covers those phases.

## Self-review checklist

- Spec coverage: collector contract, unknown fallbacks, redaction, atomic persistence, startup logging, non-fatal failures, and focused tests each have explicit tasks.
- Placeholder scan: no unbounded implementation requirement is used; the actual startup path must be identified before editing because the code index did not expose the entrypoint directly.
- Type consistency: `collect_provenance()` returns `dict[str, Any]`; `persist_provenance()` returns `Path`; startup helper returns the collected dictionary.
- Scope: no Compose, image, report, or image-enhancement changes are included.
