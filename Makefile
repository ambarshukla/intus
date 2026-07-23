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

.PHONY: help sync fmt lint test check generate catalog clean

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
	cd gen && uv run ruff format .

lint: ## ruff format check + lint (what CI runs)
	cd gen && uv run ruff format --check . && uv run ruff check .

test: ## run the test suite
	cd gen && uv run pytest

check: lint test ## lint and test — the pre-commit gate

generate: ## generate the Halcyon dataset into data/raw (SCALE=small for a quick run)
	cd gen && uv run intus-gen generate --scale $(SCALE) --seed $(SEED) --out ../$(OUT)

catalog: ## regenerate the sensitivity catalog (docs/data-catalog.md) from the schemas
	cd gen && uv run intus-gen catalog --out ../docs/data-catalog.md

clean: ## remove generated data
	rm -rf $(OUT)
