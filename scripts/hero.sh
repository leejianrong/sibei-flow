#!/usr/bin/env bash
# Seam-3 hero-pipeline driver (docs/design/HERO-PIPELINE.md).
#
# Subcommands (wrapped by the Makefile `hero` / `hero-break` / `hero-down`):
#   up      bring up the hero stack, seed the HEALTHY pre-rename state, run the
#           analytics_daily DAG to a GREEN finish.
#   break   rename customer_id -> cust_id (the drift), re-trigger the DAG; the
#           dbt_build_orders task fails for real -> callback -> brain -> worker
#           heals -> a verified pr_proposed run appears in the dashboard.
#   down    stop the hero stack.
#
# Idempotent and offline (replay LLM by default; no API key needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --profile hero)
DAG=analytics_daily
BRAIN="${SBFLOW_WEBHOOK_URL:-http://localhost:8080}"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

wh_psql() { "${COMPOSE[@]}" exec -T warehouse psql -v ON_ERROR_STOP=1 -U sibei -d warehouse "$@"; }
af() { "${COMPOSE[@]}" exec -T airflow "$@"; }

wait_warehouse() {
  for _ in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T warehouse pg_isready -U sibei -d warehouse >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "warehouse never became ready" >&2; exit 1
}

wait_airflow() {
  say "Waiting for Airflow to register the $DAG DAG (first boot migrates the metadata DB; up to a few min)"
  for _ in $(seq 1 120); do
    if af airflow dags list 2>/dev/null | grep -q "$DAG"; then
      echo "DAG registered"; return 0
    fi
    sleep 3
  done
  echo "Airflow never registered the DAG" >&2; exit 1
}

# Trigger a fresh run with a known run_id and wait for it to reach a terminal
# state. Args: <run_id> <expected_state: success|failed>
trigger_and_wait() {
  local run_id="$1" expect="$2"
  af airflow dags trigger "$DAG" --run-id "$run_id" >/dev/null
  echo "triggered run $run_id; waiting for it to finish…"
  for _ in $(seq 1 90); do
    local state
    state="$(af airflow dags state "$DAG" "$run_id" 2>/dev/null | tr -d '\r' | tail -n1 || true)"
    case "$state" in
      success|failed)
        echo "run $run_id -> $state"
        [ "$state" = "$expect" ] && return 0
        echo "expected $expect but got $state" >&2; return 1
        ;;
    esac
    sleep 2
  done
  echo "run $run_id did not finish in time" >&2; return 1
}

cmd="${1:-help}"
case "$cmd" in
  up)
    say "Bringing up the hero stack (core + Airflow + dbt + git remote)"
    "${COMPOSE[@]}" up -d --build
    wait_warehouse
    say "Seeding the HEALTHY pre-rename warehouse state (customer_id present)"
    wh_psql < db/warehouse/hero_seed.sql
    wait_airflow
    say "Running $DAG — expecting GREEN (customer_id still present)"
    trigger_and_wait "hero_green_$(date +%s)" success
    say "Healthy baseline established. Airflow UI: http://localhost:8081 (admin/admin)"
    echo "Next: 'make hero-break' to rename the column and watch sibei-flow heal it."
    ;;
  break)
    say "Applying the drift: RENAME raw.raw_customers.customer_id -> cust_id"
    wh_psql < db/warehouse/hero_break.sql
    say "Re-running $DAG — expecting dbt_build_orders to FAIL for real"
    if trigger_and_wait "hero_break_$(date +%s)" failed; then
      say "As expected: the run FAILED. The on_failure_callback POSTed the drift to the brain."
    else
      echo "WARNING: the break run did not fail as expected — check the DAG/warehouse." >&2
    fi
    say "sibei-flow is now healing it. Watch the dashboard: ${BRAIN}/"
    echo "Within ~seconds the schema-drift run should reach outcome=pr_proposed with a"
    echo "minimal customer_id -> cust_id diff + verification evidence. Poll it with:"
    echo "  curl -s ${BRAIN}/api/runs | python3 -m json.tool"
    ;;
  down)
    say "Stopping the hero stack"
    "${COMPOSE[@]}" down
    ;;
  *)
    echo "usage: scripts/hero.sh {up|break|down}" >&2; exit 2
    ;;
esac
