#!/usr/bin/env bash
# Start the local HTTP CONNECT -> SOCKS5h tunnel for .onion fetches.
# Idempotent: if the proxy is already running, do nothing.
set -euo pipefail

PID_FILE="${ONION_PROXY_PID_FILE:-/tmp/onion-connect-proxy.pid}"
LOG_FILE="${ONION_PROXY_LOG_FILE:-/tmp/onion-connect-proxy.log}"

# If pid file exists and the process is alive, nothing to do.
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "OnionConnectProxy already running (pid $(cat "$PID_FILE"))"
    exit 0
fi

# Start fresh.
nohup .venv/bin/python -m local_deep_research.network.onion_connect_proxy \
    >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
sleep 1
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "OnionConnectProxy started (pid $(cat "$PID_FILE"), log $LOG_FILE)"
else
    echo "OnionConnectProxy failed to start; see $LOG_FILE" >&2
    exit 1
fi