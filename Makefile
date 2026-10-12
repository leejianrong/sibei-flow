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
# Mirror docker-compose's own project naming: respect an explicitly-set
# COMPOSE_PROJECT_NAME, else fall back to the basename of the directory
# containing the compose file (compose's own default), same fix as
# docker-compose.yml's SANDBOX_NETWORK (KAN-1016, ada06cf). A hardcoded
# `sibei-flow_default` only exists when the compose project is literally
# named "sibei-flow" — it breaks under worktree isolation or a CI matrix,
# where the project name differs.
NET   := $(if $(COMPOSE_PROJECT_NAME),$(COMPOSE_PROJECT_NAME),$(notdir $(CURDIR)))_default
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
        hero hero-break hero-down logs-airflow eval \
        web-lint web-typecheck web-test web-build

# Frontend (web/, Svelte + Vite + TypeScript). The web-* checks run from the
# existing lint / typecheck / test-fast gates (so the pre-push hook covers them)
# but are skipped, with a message, when npm is not installed, so a Python-only
# contributor is not blocked. CI always runs them (the `web` job).
HAVE_NPM := $(shell command -v npm 2>/dev/null)
WEB_SKIP  = @echo "skipping web: npm not found (install Node, see web/.nvmrc)"

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
test-fast: ## Fast pre-push gate: no-infra worker tests + web unit tests
	cd worker && uv sync --extra dev && uv run pytest -m "not infra"
	$(if $(HAVE_NPM),$(MAKE) web-test,$(WEB_SKIP))

lint: ## Lint the worker (ruff) and the web UI (eslint) — non-mutating check
	cd worker && uvx ruff@0.8.4 check .
	$(if $(HAVE_NPM),$(MAKE) web-lint,$(WEB_SKIP))

typecheck: ## Type-check the worker (mypy) and the web UI (svelte-check) — BLOCKING
	cd worker && uv run --with mypy mypy sbflow_worker
	$(if $(HAVE_NPM),$(MAKE) web-typecheck,$(WEB_SKIP))

# Install web deps from the lockfile only when missing or the lockfile changed.
web/node_modules: web/package-lock.json
	cd web && npm ci
	@touch web/node_modules

web-lint: web/node_modules ## ESLint the web UI
	cd web && npm run lint

web-typecheck: web/node_modules ## svelte-check the web UI
	cd web && npm run typecheck

web-test: web/node_modules ## Vitest unit tests for the web UI
	cd web && npm test

web-build: web/node_modules ## Build the web UI to one self-contained dist/index.html
	cd web && npm run build

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

# --- eval harness (SIB-81) ---------------------------------------------------
# Runs every case under evals/cases/ through the real worker path (see
# evals/README.md). Keyless by default (replay provider = a plumbing smoke test);
# for a real-model run pass LLM_PROVIDER=claude and export ANTHROPIC_API_KEY.
#   make eval                       all cases
#   make eval ONLY=rename_customer_id
#   make eval LLM_PROVIDER=claude SBFLOW_SATAY_LOOP=1 SBFLOW_SATAY_CANDIDATES=3
EVAL_ADMIN_URL := postgres://sibei:sibei@warehouse:5432/warehouse
eval: sandbox ## Run the eval corpus through the real repair path (see evals/README.md)
	$(COMPOSE) build worker
	$(COMPOSE) up -d postgres warehouse brain
	mkdir -p evals/reports
	docker run --rm --network $(NET) \
	  -v "$(CURDIR)":/repo \
	  -v $(WORKER_VENV):/repo/worker/.venv \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  -v $(SANDBOX_WORK):$(SANDBOX_WORK) \
	  -w /repo/worker \
	  -e DATABASE_URL=$(DB_URL) -e WAREHOUSE_URL=$(WH_URL) \
	  -e SAMPLE_WAREHOUSE_URL=$(SAMPLE_URL) -e WAREHOUSE_ADMIN_URL=$(EVAL_ADMIN_URL) \
	  -e SANDBOX_NETWORK=$(NET) -e SANDBOX_WORK_DIR=$(SANDBOX_WORK) \
	  -e SBFLOW_BRAIN_URL=http://brain:8080 \
	  -e LLM_PROVIDER -e LLM_MODEL -e LLM_BASE_URL -e ANTHROPIC_API_KEY -e OPENAI_API_KEY \
	  -e SBFLOW_SATAY_LOOP -e SBFLOW_SATAY_CANDIDATES -e SBFLOW_DASHBOARD_TOKEN \
	  -e EVAL_COMMIT=$$(git rev-parse --short HEAD 2>/dev/null) \
	  -e HOST_UID=$$(id -u) \
	  $(WORKER_IMG) sh -c "uv sync --extra dev && \
	    uv run python -m sbflow_worker.evalkit --out /repo/evals/reports/report.json \
	      $(foreach c,$(ONLY),--only $(c)); \
	    rc=\$$?; chown -R \$$HOST_UID /repo/evals/reports 2>/dev/null; exit \$$rc"
