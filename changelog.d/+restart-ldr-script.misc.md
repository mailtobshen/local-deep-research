**Added `scripts/dev/restart-ldr.sh` for the local dev container.**

A one-shot bash wrapper around `docker restart ldr-local` that also
verifies (1) container became healthy within 30s, (2) the in-container
`source_sha` provenance matches `git HEAD` (catches stale hot_mount /
forgotten `git push`), and (3) Flask responds to `GET /`. Exits
non-zero on any check failure so it can be chained in CI / dev hooks
as `git push && bash restart-ldr.sh`. Container name and port are
overridable via `LDR_CONTAINER` / `LDR_PORT` env vars.
