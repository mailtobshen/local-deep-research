# Default IMG-TRACE Candidate Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ldr-local` expose `LDR_IMG_TRACE_CANDIDATES=1` by default while allowing an explicit host-side override.

**Architecture:** Add one Compose environment entry to the existing `local-deep-research` service. Use Compose default interpolation so an unset host variable resolves to `1`, while values such as `0` remain available as explicit overrides. No Python code changes are required because `postprocessing.py` already reads this process environment variable.

**Tech Stack:** Docker Compose YAML, shell-based configuration validation

## Global Constraints

- Modify only `docker-compose.ldr-local.yml`.
- Preserve the existing `ldr_local_data` named volume and source hot mounts.
- Default `LDR_IMG_TRACE_CANDIDATES` to the exact string `1`.
- Do not modify or stage the pre-existing change in `src/local_deep_research/web/routes/research_routes.py`.
- Recreating `ldr-local` must not recreate or replace the auxiliary SearXNG/Tor services.

---

### Task 1: Enable Candidate Trace Logging by Default

**Files:**
- Modify: `docker-compose.ldr-local.yml:66-81`

**Interfaces:**
- Consumes: Docker Compose `${VARIABLE:-default}` interpolation and `postprocessing.py`'s existing exact check `os.getenv("LDR_IMG_TRACE_CANDIDATES") == "1"`.
- Produces: A `local-deep-research` container environment where `LDR_IMG_TRACE_CANDIDATES` resolves to `1` unless explicitly overridden.

- [ ] **Step 1: Capture the current failing configuration check**

Run:

```bash
docker compose -f docker-compose.ldr-local.yml --profile ldr-local config \
  | grep 'LDR_IMG_TRACE_CANDIDATES'
```

Expected: no matching line and a non-zero grep exit status.

- [ ] **Step 2: Add the minimal Compose environment entry**

Add this block after the network proxy environment entries:

```yaml
      # --- IMG-TRACE 候选明细: 默认开启，可由宿主显式设为 0 关闭 ---
      - LDR_IMG_TRACE_CANDIDATES=${LDR_IMG_TRACE_CANDIDATES:-1}
```

- [ ] **Step 3: Verify the default resolved Compose value**

Run:

```bash
env -u LDR_IMG_TRACE_CANDIDATES \
  docker compose -f docker-compose.ldr-local.yml --profile ldr-local config \
  | grep 'LDR_IMG_TRACE_CANDIDATES'
```

Expected:

```text
LDR_IMG_TRACE_CANDIDATES: "1"
```

- [ ] **Step 4: Verify an explicit override is preserved**

Run:

```bash
LDR_IMG_TRACE_CANDIDATES=0 \
  docker compose -f docker-compose.ldr-local.yml --profile ldr-local config \
  | grep 'LDR_IMG_TRACE_CANDIDATES'
```

Expected:

```text
LDR_IMG_TRACE_CANDIDATES: "0"
```

- [ ] **Step 5: Recreate only the LDR service**

Run:

```bash
docker compose -f docker-compose.ldr-local.yml --profile ldr-local \
  up -d --force-recreate --no-deps local-deep-research
```

Expected: `ldr-local` is recreated and reaches a running state; `searxng-ldr` and `ldr-tor` are not recreated.

- [ ] **Step 6: Verify the live container sees the exact enabled value**

Run:

```bash
docker exec ldr-local /install/.venv/bin/python -c \
  'import os; print(repr(os.getenv("LDR_IMG_TRACE_CANDIDATES")))'
```

Expected:

```text
'1'
```

- [ ] **Step 7: Verify container health and inspect the focused diff**

Run:

```bash
docker ps --filter name='^/ldr-local$' --format '{{.Names}} {{.Status}}'
git diff -- docker-compose.ldr-local.yml
git status --short
```

Expected:

- `ldr-local` becomes `healthy` after startup.
- The Compose diff contains only the new comment and environment entry.
- `src/local_deep_research/web/routes/research_routes.py` remains modified but untouched by this task.

- [ ] **Step 8: Commit only if explicitly requested**

Do not commit by default. If the user explicitly requests a commit:

```bash
git add docker-compose.ldr-local.yml
git commit -m "chore(images): enable candidate IMG-TRACE by default

Co-Authored-By: Claude <noreply@anthropic.com>"
```
