# Runtime Provenance — Phase 1 Design

## Goal

Make the currently running LDR process identifiable without changing its existing Docker startup mode, source hot-mount, image selection, volumes, auxiliary services, or image-enhancement behavior.

## Scope

Phase 1 adds observability only:

- Capture application/source identity at startup.
- Persist a JSON snapshot under `/data/runtime/provenance.json` when `/data` is available.
- Emit a redacted, structured startup summary to the existing application logger.
- Expose the snapshot through the existing health/diagnostic surface only if that surface can be extended without weakening its authentication boundary.
- Add focused tests for field presence, JSON serialization, import-path identity, and unavailable metadata.

Phase 1 explicitly does not:

- Remove or change the source bind mount.
- Introduce immutable-image Compose mode.
- Change image tags or dependency pinning.
- Fail startup when provenance fields are unknown.
- Change report/image-selection logic.
- Store provenance in report records yet.

## Provenance contract

The snapshot uses stable, JSON-serializable fields:

- `event`: `ldr_startup_provenance`
- `captured_at`: UTC timestamp
- `package_version`: installed application version, when available
- `python_version`: runtime Python version
- `module_file`: resolved `local_deep_research` import path
- `source_root`: best-effort application source root
- `source_sha`: best-effort Git commit for the source root, otherwise `unknown`
- `working_tree_dirty`: `true`, `false`, or `unknown`
- `runtime_mode`: `hot_mount`, `immutable`, or `unknown`, inferred from an explicit environment value when present and otherwise `unknown`
- `build_sha`: build-provided environment value when present, otherwise `unknown`
- `image_ref`: deployment-provided environment value when present, otherwise `unknown`
- `data_dir`: configured data directory

No API keys, passwords, proxy URLs containing credentials, or full environment dumps may be included.

Unknown metadata is represented explicitly as `unknown`; it is not silently omitted and does not block startup in this phase.

## Data flow

1. Application startup calls a small provenance collector with explicit inputs and safe environment lookups.
2. The collector resolves the installed package path and derives a source root without assuming that `.git` exists inside the container.
3. It reads optional build/deployment identity variables, using `unknown` when absent.
4. It writes the snapshot atomically to `/data/runtime/provenance.json` when possible. A filesystem failure is logged but does not prevent application startup.
5. It emits a concise structured log containing the event, commit/build/image identifiers, runtime mode, module path, and persistence result.
6. A diagnostic/health response may include the non-sensitive snapshot or a stable subset, subject to the existing endpoint's access policy.

## Error handling

- Missing `.git`, missing environment values, or inability to resolve a commit produce `unknown` fields.
- Permission, filesystem, or serialization errors are logged with context and do not abort startup.
- Values are normalized to strings, booleans, or null-safe primitives before serialization.
- The collector must not execute arbitrary shell input or log raw environment contents.

## Testing

Tests should cover:

1. Complete metadata produces valid JSON with all contract keys.
2. Missing Git metadata yields `source_sha=unknown` without raising.
3. Missing optional build/image variables yields explicit `unknown` values.
4. The module path is the actual imported package path.
5. Atomic persistence creates the expected file and preserves valid JSON.
6. Persistence failure is non-fatal.
7. Sensitive environment values are absent from the emitted payload.

The first implementation should run the focused provenance tests and the existing health/startup tests. Docker restart/rebuild is intentionally deferred until the implementation is verified locally.

## Future phases

- Phase 2: explicit hot-mount versus immutable Compose profiles and source/image compatibility gates.
- Phase 3: commit-tagged images, pinned auxiliary service digests, and per-worktree runtime isolation.
- Phase 4: attach provenance snapshots to report metadata and add report/image smoke tests, including a non-zero image insertion contract.
