#!/usr/bin/env bash
# Headless smoke test: theme manager + stub producer over HTTP (no hardware, no trcc).
# Writes two temp configs, starts both services, then asserts the HTTP contract via
# curl. Exits non-zero if any check FAILs. No trcc / probe_* is ever invoked.
set -euo pipefail

PORT="${PORT:-18123}"
BASE="http://127.0.0.1:${PORT}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
else
    PY="${PYTHON:-python3}"
fi
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

TMP="$(mktemp -d)"
MANAGER_PID=""
STUB_PID=""
FAILURES=0

log()  { printf '%s\n' "$*"; }
pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; FAILURES=$((FAILURES + 1)); }

cleanup() {
    if [ -n "$STUB_PID" ]; then kill "$STUB_PID" 2>/dev/null || true; fi
    if [ -n "$MANAGER_PID" ]; then kill "$MANAGER_PID" 2>/dev/null || true; fi
    wait 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

log "== write temp configs =="
cat > "$TMP/manager.yaml" <<EOF
# Headless smoke manager (NO trcc: theme_cmd is null).
host: 127.0.0.1
port: $PORT
state_file: $TMP/manager_state.json
body_limit_bytes: 500
switch_timeout_s: 5.0
default_mode: day
themes:
  day: d037
  night: d029
adapter:
  day:
    background: "#f2f3f7"
    panel: "#ffffff"
    accent: "#2e6fdb"
    text: "#1a1a1e"
    muted: "#6b7280"
  night:
    background: "#111318"
    panel: "#1b1e27"
    accent: "#4f9cf9"
    text: "#eef2f8"
    muted: "#8a93a6"
theme_cmd: null
auth_token: null
EOF

cat > "$TMP/stubdata.yaml" <<EOF
# Headless smoke producer (no hardware).
manager_url: $BASE
poll_s: 0.5
seed: 7
num_gauges: 4
switch_probability: 0.0
value_min: 0.0
value_max: 100.0
EOF

json_field() {
    "$PY" -c "import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1], ''))" "$1"
}

http_code() {
    curl -s -o /dev/null -w '%{http_code}' "$@"
}

log "== start manager on $BASE =="
"$PY" -m manager --config "$TMP/manager.yaml" --host 127.0.0.1 --port "$PORT" >"$TMP/manager.log" 2>&1 &
MANAGER_PID=$!

ready=0
for i in $(seq 1 40); do
    if curl -fsS "$BASE/health" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.5
done
if [ "$ready" -ne 1 ]; then
    fail "manager ready on $BASE"
    log "--- manager.log ---"
    cat "$TMP/manager.log" 2>/dev/null || true
    exit 1
fi
pass "manager ready ($BASE/health)"

log "== start stub producer =="
"$PY" -m stubdata --config "$TMP/stubdata.yaml" >"$TMP/stubdata.log" 2>&1 &
STUB_PID=$!
sleep 2

log "== contract checks =="

health="$(curl -fsS "$BASE/health")"
status="$(printf '%s' "$health" | json_field status)"
seq0="$(printf '%s' "$health" | json_field seq)"
seq_ok=0
if [ "$seq0" -gt 0 ] 2>/dev/null; then seq_ok=1; fi
[ "$status" = "ok" ] && pass "/health status=$status" || fail "/health status=$status"
[ "$seq_ok" -eq 1 ] && pass "/health seq=$seq0 (positive)" || fail "/health seq=$seq0 not positive"

code="$(http_code -X POST "$BASE/dashboard/update" -H 'Content-Type: application/json' -d '{"values":{"cpu":42}}')"
[ "$code" = "200" ] && pass "POST /dashboard/update -> $code" || fail "POST /dashboard/update -> $code"

theme="$(curl -s -X POST "$BASE/theme" -H 'Content-Type: application/json' -d '{"mode":"night"}')"
tmode="$(printf '%s' "$theme" | json_field mode)"
taction="$(printf '%s' "$theme" | json_field active_theme)"
[ "$tmode" = "night" ] && pass "POST /theme mode=$tmode" || fail "POST /theme mode=$tmode"
[ "$taction" = "d029" ] && pass "POST /theme active_theme=$taction" || fail "POST /theme active_theme=$taction"

hmode="$(curl -fsS "$BASE/health" | json_field mode)"
[ "$hmode" = "night" ] && pass "GET /health persists mode=$hmode" || fail "GET /health persists mode=$hmode"

BIG="$(printf 'x%.0s' {1..600})"
code413="$(http_code -X POST "$BASE/dashboard/update" -H 'Content-Type: application/json' -d "{\"values\":{\"a\":\"$BIG\"}}")"
[ "$code413" = "413" ] && pass "oversized payload -> $code413" || fail "oversized payload -> $code413"

STATE_FILE="$TMP/manager_state.json"
if [ -f "$STATE_FILE" ]; then
    pass "state file exists"
else
    fail "state file missing: $STATE_FILE"
fi

seq_a="$( "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['seq'])" "$STATE_FILE" 2>/dev/null || echo 0 )"
curl -s -X POST "$BASE/dashboard/update" -H 'Content-Type: application/json' -d '{"values":{"ram":5}}' >/dev/null || true
seq_b="$( "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['seq'])" "$STATE_FILE" 2>/dev/null || echo 0 )"
if [ "$seq_b" -gt "$seq_a" ] 2>/dev/null; then
    pass "state file seq grew $seq_a -> $seq_b"
else
    fail "state file seq did not grow ($seq_a -> $seq_b)"
fi

log ""
if [ "$FAILURES" -eq 0 ]; then
    log "ALL CHECKS PASSED"
else
    log "$FAILURES CHECK(S) FAILED"
fi
exit "$FAILURES"