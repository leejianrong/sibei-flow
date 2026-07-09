#!/usr/bin/env bash
# End-to-end V1 demo: POST an in-scope failure and an out-of-scope failure,
# then show what the dashboard API records.
#
# Prereq:  docker compose up --build   (in another terminal, or -d)
# Usage:   ./scripts/demo.sh
set -euo pipefail

BRAIN="${SBFLOW_WEBHOOK_URL:-http://localhost:8080}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

say "Waiting for the brain to be healthy at ${BRAIN}"
for i in $(seq 1 60); do
  if curl -fsS "${BRAIN}/healthz" >/dev/null 2>&1; then echo "brain is up"; break; fi
  sleep 1
done

say "POST an IN-SCOPE schema-drift failure -> queued, then the agent drafts a fix"
DRIFT=$(curl -fsS -X POST "${BRAIN}/webhook" \
  -H 'content-type: application/json' \
  --data @"${ROOT}/fixtures/schema_drift_failure.json")
echo "$DRIFT"
DRIFT_ID=$(printf '%s' "$DRIFT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

say "POST an OUT-OF-SCOPE timeout failure -> should be recorded, NOT dispatched"
curl -fsS -X POST "${BRAIN}/webhook" \
  -H 'content-type: application/json' \
  --data @"${ROOT}/fixtures/timeout_failure.json"
echo

say "Give the worker a moment to claim + run the agent loop"
sleep 6

say "GET /api/runs  (run history)"
curl -fsS "${BRAIN}/api/runs" | python3 -m json.tool

say "GET /api/runs/${DRIFT_ID}  (the drafted fix — diff + explanation, unverified)"
curl -fsS "${BRAIN}/api/runs/${DRIFT_ID}" | python3 -c '
import sys, json
d = json.load(sys.stdin)
r = d.get("result") or {}
print("outcome :", d.get("outcome"))
print("evidence:", r.get("evidence"), "(null => unverified until V3)")
print("explanation:", (r.get("explanation") or "")[:200])
print("--- diff ---")
print(r.get("diff") or "(none)")'

say "Open the dashboard UI:  ${BRAIN}/"
echo "Expected: the schema-drift run shows outcome=pr_proposed with a minimal"
echo "          customer_id -> cust_id diff, marked UNVERIFIED; the timeout run"
echo "          shows outcome=out_of_scope (never queued)."
