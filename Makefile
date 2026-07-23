# Task runner for common dev commands — one documented place for them,
# identical across machines and sessions.
#
# .env (gitignored) supplies machine-specific values; -include tolerates its
# absence so a fresh clone works with no setup beyond `uv sync`.
-include .env

# Generation knobs. `?=` defers to the environment, so CI can run
# `SCALE=small make generate` and reuse this target verbatim rather than
# maintaining a second, drift-prone command.
SCALE ?= full
SEED  ?= 20260723
OUT   ?= data/raw

# `--env-file .env` is passed only when .env exists (compose errors on a
# missing file); without it the compose file's ${VAR:-default} values apply.
COMPOSE = docker compose -f infra/docker-compose.yml $(if $(wildcard .env),--env-file .env)
PGUSER ?= intus
PGDB   ?= intus

.PHONY: help sync fmt lint test check generate catalog clean \
        up down psql db-status db-clean migrate load build dq-score warehouse \
        land deploy-job run-job

# Two traps here, both of which have bitten on the sibling project:
#  -h        MAKEFILE_LIST is "Makefile .env" (from -include above), and grep
#            prefixes every match with its filename once given more than one
#            file — which awk then reads as the target name.
#  [a-z0-9-] the character class must cover every character a target name can
#            contain, or that target silently vanishes from the help.
help: ## show available targets
	@grep -hE '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

sync: ## install/refresh the workspace virtualenv from uv.lock
	uv sync

fmt: ## format all Python in place
	uv run ruff format gen warehouse lakehouse

lint: ## ruff format check + lint (what CI runs)
	uv run ruff format --check gen warehouse lakehouse && uv run ruff check gen warehouse lakehouse

test: ## run every test suite
	cd gen && uv run pytest
	cd warehouse && uv run pytest
	cd lakehouse && uv run pytest

check: lint test ## lint and test — the pre-commit gate

generate: ## generate the Halcyon dataset into data/raw (SCALE=small for a quick run)
	cd gen && uv run intus-gen generate --scale $(SCALE) --seed $(SEED) --out ../$(OUT)

catalog: ## regenerate the sensitivity catalog (docs/data-catalog.md) from the schemas
	cd gen && uv run intus-gen catalog --out ../docs/data-catalog.md

clean: ## remove generated data
	rm -rf $(OUT)

# --------------------------------------------------------------------------
# Legacy warehouse (Postgres in Docker, port 5433)
# --------------------------------------------------------------------------

up: ## start Postgres and wait until it accepts connections
	$(COMPOSE) up -d --wait

down: ## stop Postgres, keeping its data volume
	$(COMPOSE) down

psql: ## open a psql shell in the container
	$(COMPOSE) exec postgres psql -U $(PGUSER) -d $(PGDB)

db-status: ## show connection and migration state
	cd warehouse && uv run intus-wh status

db-clean: ## stop Postgres AND delete its data volume
	$(COMPOSE) down -v

migrate: ## apply pending SQL migrations
	cd warehouse && uv run intus-wh migrate

load: ## truncate and reload staging from data/raw
	cd warehouse && uv run intus-wh load --from ../$(OUT)

build: ## run the transforms that build the star schema from staging
	cd warehouse && uv run intus-wh build

dq-score: ## score detected exceptions against the generator's defect manifest
	cd warehouse && uv run intus-wh dq-score --from ../$(OUT)

warehouse: up migrate load build dq-score ## full local rebuild: start, migrate, load, build, score

# --------------------------------------------------------------------------
# Lakehouse (Databricks; shared Free Edition workspace, catalog `intus`)
# --------------------------------------------------------------------------

land: ## upload data/raw/*.csv to the Unity Catalog landing volume (needs DATABRICKS_HOST in .env)
	@test -n "$(DATABRICKS_HOST)" || { echo "DATABRICKS_HOST not set — copy .env.example to .env and fill it in"; exit 1; }
	for f in $(OUT)/*.csv; do databricks fs cp "$$f" "dbfs:/Volumes/intus/landing/raw/$$(basename $$f)" --overwrite; done

deploy-job: ## deploy the Databricks bundle in databricks.yml (needs DATABRICKS_HOST)
	@test -n "$(DATABRICKS_HOST)" || { echo "DATABRICKS_HOST not set — copy .env.example to .env and fill it in"; exit 1; }
	databricks bundle deploy

# Only meaningful once databricks.yml's git_source branch (main) actually
# contains whatever this points at — a bundle change that adds a task
# pointing at a new SQL file must be merged before this runs, or the job
# checks out main, finds nothing there, and fails outright.
run-job: ## run the lakehouse build now (needs deploy-job to have run against a merged main)
	@test -n "$(DATABRICKS_HOST)" || { echo "DATABRICKS_HOST not set — copy .env.example to .env and fill it in"; exit 1; }
	databricks bundle run lakehouse_build
