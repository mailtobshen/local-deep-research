#!/bin/bash
# restart-ldr.sh — one-shot restart of the ldr-local WebUI container with
# full verification, for picking up src/** changes inside the hot-mounted
# container (Python bytecode doesn't reload until PID-1 is recycled).
#
# Standard flow:
#   1. Confirm the container exists (fail fast if docker compose isn't up)
#   2. Restart it
#   3. Wait for healthy
#   4. Verify source_sha == git HEAD  (proves hot_mount picked up the
#      latest commit; if it's stale, your commit didn't actually land
#      on the host's working tree the container is mounted from)
#   5. HTTP probe
#
# Gotcha: container name is `ldr-local`, NOT `local-deep-research`
# (the latter is the service name in docker-compose.yml; compose adds
# the `-ldr` suffix because the compose file is docker-compose.ldr-local.yml
# and uses the `ldr-local` profile). Don't blindly `docker restart
# local-deep-research` — it will silently succeed against an empty
# filter and nothing restarts.

set -euo pipefail

CONTAINER="${LDR_CONTAINER:-ldr-local}"
PORT="${LDR_PORT:-5000}"
EXPECTED_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# --- Step 1: preflight ---------------------------------------------------------
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ERROR: container '$CONTAINER' not found." >&2
    echo "  Running containers:" >&2
    docker ps --format '  - {{.Names}}\t{{.Image}}\t{{.Status}}' >&2
    exit 1
fi

# --- Step 2: restart -----------------------------------------------------------
echo "[1/5] Restarting $CONTAINER ..."
docker restart "$CONTAINER" >/dev/null

# --- Step 3: wait for healthy --------------------------------------------------
echo "[2/5] Waiting for healthy state ..."
for i in $(seq 1 30); do
    status="$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
    if [ "$status" = "healthy" ]; then
        break
    fi
    sleep 1
done
if [ "$status" != "healthy" ]; then
    echo "ERROR: container did not become healthy within 30s (last status: $status)" >&2
    echo "  Tail of logs:" >&2
    docker logs "$CONTAINER" --tail 30 >&2
    exit 1
fi

# --- Step 4: print status + verify source_sha ---------------------------------
echo "[3/5] Container status:"
docker ps --filter "name=$CONTAINER" \
    --format "  {{.Names}}\t{{.Status}}\t{{.RunningFor}}\t{{.CreatedAt}}"

echo "[4/5] Health / restart count / StartedAt:"
docker inspect --format='  Health={{.State.Health.Status}}  Restarts={{.RestartCount}}  StartedAt={{.State.StartedAt}}' \
    "$CONTAINER"

echo "[5/5] Provenance (source_sha should match git HEAD: ${EXPECTED_SHA:0:12}):"
# `|| true` because grep returns 1 if no match — that's informational, not fatal.
sha="$(docker logs "$CONTAINER" --tail 200 2>&1 | grep -oE "'source_sha': '[0-9a-f]{40}'" | tail -1 || true)"
if [ -z "$sha" ]; then
    echo "  WARN: could not find source_sha in container logs (yet?)"
else
    echo "  $sha"
    actual="$(echo "$sha" | grep -oE '[0-9a-f]{40}')"
    if [ "$EXPECTED_SHA" != "unknown" ] && [ "$actual" != "$EXPECTED_SHA" ]; then
        echo "  ERROR: container source_sha ($actual) != git HEAD ($EXPECTED_SHA)" >&2
        echo "  Hint: the container's hot_mount points at a different working tree," >&2
        echo "  or you forgot to git push / git pull before restarting." >&2
        exit 1
    fi
fi

# --- Step 5: HTTP probe -------------------------------------------------------
probe_code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/" || echo 000)"
echo "[probe] GET http://localhost:$PORT/ -> HTTP $probe_code (expect 302 redirect)"

if [ "$probe_code" != "302" ] && [ "$probe_code" != "200" ]; then
    echo "ERROR: unexpected probe status — Flask may not be serving yet" >&2
    exit 1
fi

echo "OK. Container restarted with $(echo "$sha" | grep -oE '[0-9a-f]{40}' || echo unknown)."
