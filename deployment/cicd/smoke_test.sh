#!/usr/bin/env bash
# Post-deployment smoke test. Usage: BASE_URL=https://copilot.example.com bash smoke_test.sh
set -euo pipefail
: "${BASE_URL:?BASE_URL is required}"
BASE_URL="${BASE_URL%/}"

check() {  # check <description> <expected-status> <url> [extra curl args...]
  local desc="$1" expected="$2" url="$3"; shift 3
  local status
  status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@" "$url")
  if [ "$status" != "$expected" ]; then
    echo "FAIL: $desc (expected $expected, got $status) $url" >&2
    exit 1
  fi
  echo "ok:   $desc"
}

for attempt in $(seq 1 20); do  # wait for the rollout to become reachable
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/health/live" || true)" = "200" ]; then break; fi
  [ "$attempt" = 20 ] && { echo "FAIL: backend not reachable" >&2; exit 1; }
  sleep 6
done

check "liveness"                         200 "$BASE_URL/health/live"
check "readiness (database + redis)"     200 "$BASE_URL/health/ready"
check "API rejects anonymous requests"   401 "$BASE_URL/api/v1/incidents"
check "public auth config"               200 "$BASE_URL/api/v1/auth/config"
check "frontend is served"               200 "$BASE_URL/"
echo "Smoke test passed for $BASE_URL"
