-include .env
export

COMPOSE := docker compose --env-file .env -f docker/compose.yaml
ENV     ?= dev
AS_OF   ?= $(shell date +%F)

.PHONY: help setup lint test local-up local-down local-logs ci ingest fixture eval up down status

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup: ## install dependencies and git hooks
	uv sync && uv run pre-commit install

lint: ## ruff + mypy
	uv run ruff check . && uv run ruff format --check . && uv run mypy src

test: ## fast unit and property tests
	uv run pytest -m "not eval and not integration" --cov=portfolio_intel --cov-fail-under=85

local-up: ## build the image and start the stack; waits for health checks
	mkdir -p audit && chmod a+rwx audit   # the containers run as uid 10001
	$(COMPOSE) up -d --build --wait

local-down: ## stop the local stack
	$(COMPOSE) down

local-logs: ## tail container logs
	$(COMPOSE) logs -f --tail=100

ci: lint test local-up ## exactly what GitHub CI runs; do this before every push
	uv run pytest -m integration
	uv run python scripts/smoke.py http://localhost:8000 http://localhost:8001/mcp
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest \
	  image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed pi:local

ingest: ## build a real data snapshot (needs internet)
	uv run pi-ingest --as-of $(AS_OF) --out data/portfolio.duckdb

fixture: ## rebuild the small committed test snapshot
	uv run pi-ingest --as-of $(AS_OF) --fixture --out data/fixtures/portfolio_small.duckdb

eval: local-up ## golden PM questions against the local stack (calls the Anthropic API)
	uv run pytest -m eval

up: ## create the AWS runtime from the last green image (about 10 min)
	scripts/env_up.sh $(ENV)

down: ## destroy the AWS runtime; data, images, audit and logs are kept
	scripts/env_down.sh $(ENV)

status: ## is the AWS runtime up, which image, when does it expire
	scripts/env_status.sh $(ENV)