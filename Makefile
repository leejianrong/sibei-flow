# sibei-flow — developer convenience targets.
#
# Wraps the repeated docker/compose/test invocations. Targets shell out to
# `docker compose`, so they track whatever the compose file defines (ports,
# services, sandbox wiring) without hardcoding it here.
#
#   make up            bring the whole stack up (build if needed)
#   make demo          drive the end-to-end demo
#   make test          run both test suites (brain + worker)
#   make down / clean  stop the stack (clean also drops volumes)
#
# No local Rust/uv toolchain is required: tests run in throwaway containers
# joined to the compose network.

COMPOSE ?= docker compose

# --- conventions (see README "Project conventions") -----------------------
NET   := sibei-flow_default
DB_URL := postgres://sibei:sibei@postgres:5432/sibei
WH_URL := postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse

# Tier-2 dev/sample connection (writable dev role, never prod) used by the
# sandbox verification tests.
SAMPLE_URL := postgres://sbflow_dev:sbflow_dev@warehouse:5432/warehouse

# Container images + cached volumes reused across test runs (speeds re-runs).
RUST_IMG      := rust:1-slim-bookworm
# Worker tests run in the built worker image (not a bare uv image) because the
# V3 sandbox tests need the docker CLI, which the worker image ships.
WORKER_IMG    := sibei-flow-worker
SANDBOX_IMG   := sbflow-sandbox:latest
BRAIN_TARGET  := sibei_brain_target
CARGO_CACHE   := sibei_cargo_registry
WORKER_VENV   := sibei_worker_venv
SANDBOX_WORK  := /tmp/sbflow-sandbox

.DEFAULT_GOAL := help
.PHONY: help up up-build build sandbox down clean ps logs logs-brain logs-worker \
        demo psql psql-warehouse test test-fast lint typecheck test-brain test-worker \
        hero hero-break hero-down logs-airflow

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- stack lifecycle -------------------------------------------------------
up: ## Start the stack detached (builds images if missing)
	$(COMPOSE) up -d

up-build: ## Rebuild images and start the stack detached
	$(COMPOSE) up -d --build

build: ## Build the brain + worker images
	$(COMPOSE) build

sandbox: ## Pre-bake the dbt verification sandbox image (cached; fast if unchanged)
	docker build -t $(SANDBOX_IMG) sandbox

down: ## Stop the stack (keep volumes)
	$(COMPOSE) down

clean: ## Stop the stack and drop volumes (fresh Postgres + warehouse next up)
	$(COMPOSE) --profile hero down -v

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

logs-brain: ## Tail the brain logs
	$(COMPOSE) logs -f --tail=100 brain

logs-worker: ## Tail the worker logs
	$(COMPOSE) logs -f --tail=100 worker

# --- hero pipeline (Seam-3 live harness: Airflow + dbt + git remote) --------
hero: ## Live hero: up Airflow+dbt+git, seed healthy state, run analytics_daily GREEN
	./scripts/hero.sh up

hero-break: ## Rename customer_id->cust_id, re-run the DAG -> real failure -> heal
	./scripts/hero.sh break

hero-down: ## Stop the hero stack (use `make clean` to also drop volumes)
	./scripts/hero.sh down

logs-airflow: ## Tail the Airflow logs
	$(COMPOSE) --profile hero logs -f --tail=100 airflow

# --- demo & poking around --------------------------------------------------
demo: ## Drive the end-to-end demo (POST failures, show the drafted result)
	./scripts/demo.sh

psql: ## Open psql against the state DB (repair_jobs)
	$(COMPOSE) exec postgres psql -U sibei -d sibei

psql-warehouse: ## Open psql against the fixture warehouse
	$(COMPOSE) exec warehouse psql -U sibei -d warehouse

# --- tests (run in throwaway containers on the compose network) ------------
test: test-brain test-worker ## Run both test suites

# The fast, no-infra lane: the pure-logic worker tests (diff guard + score
# rubric). No Postgres/warehouse/Docker, so it runs pytest directly against a
# local uv env — the pre-push gate. `-m "not infra"` deselects the infra layer.
test-fast: ## Fast pre-push gate: no-infra worker tests (no DB/warehouse/Docker)
	cd worker && uv sync --extra dev && uv run pytest -m "not infra"

lint: ## Lint the worker (ruff) — non-mutating check
	cd worker && uvx ruff@0.8.4 check .

typecheck: ## Type-check the worker (mypy) — ADVISORY until pre-existing errors are cleared
	cd worker && uv run --with mypy mypy sbflow_worker || true

test-brain: ## Seam-2 + unit tests (Rust, throwaway DB per test)
	$(COMPOSE) up -d postgres
	docker run --rm --network $(NET) \
	  -v "$(CURDIR)":/repo \
	  -v $(BRAIN_TARGET):/repo/brain/target \
	  -v $(CARGO_CACHE):/usr/local/cargo/registry \
	  -w /repo/brain -e DATABASE_URL=$(DB_URL) $(RUST_IMG) \
	  sh -c "apt-get update >/dev/null && apt-get install -y --no-install-recommends gcc libc6-dev pkg-config >/dev/null 2>&1 && cargo test"

test-worker: sandbox ## Claim + agent-loop + sandbox tests (Python + real Docker).
	$(COMPOSE) build worker
	$(COMPOSE) up -d postgres warehouse
	# Runs in the worker image (has the docker CLI) with the socket + sandbox
	# work dir mounted, so the V3 sandbox tests execute rather than skip.
	docker run --rm --network $(NET) \
	  -v "$(CURDIR)":/repo \
	  -v $(WORKER_VENV):/repo/worker/.venv \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  -v $(SANDBOX_WORK):$(SANDBOX_WORK) \
	  -w /repo/worker \
	  -e DATABASE_URL=$(DB_URL) -e WAREHOUSE_URL=$(WH_URL) \
	  -e SAMPLE_WAREHOUSE_URL=$(SAMPLE_URL) \
	  -e SANDBOX_NETWORK=$(NET) -e SANDBOX_WORK_DIR=$(SANDBOX_WORK) \
	  $(WORKER_IMG) sh -c "uv sync --extra dev && uv run pytest -q"
