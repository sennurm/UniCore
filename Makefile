# UniCore — local development
# Quick start:  make install  ->  make up  ->  make migrate  ->  make bootstrap  ->  make api (+ make web)

SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Bootstrap defaults — override like: make bootstrap UNIVERSITY_NAME="My University"
UNIVERSITY_NAME ?= Demo University
UNIVERSITY_CODE ?= UNI
ADMIN_USERNAME  ?= sadmin
ADMIN_FULLNAME  ?= Super Admin

.DEFAULT_GOAL := help

.PHONY: help install up down migrate bootstrap api web test lint type check build-web clean db-shell redis-cli

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUniCore targets:\n\n"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2} END {print ""}' $(MAKEFILE_LIST)

install: ## Create backend venv + install backend and frontend dependencies
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q -e "$(BACKEND)[dev]"
	cd $(FRONTEND) && npm install --no-audit --no-fund

up: ## Start Postgres + Redis (Docker) and wait until healthy
	docker compose up -d postgres redis
	@until docker compose exec -T postgres pg_isready -U unicore >/dev/null 2>&1; do sleep 1; done
	@echo "postgres + redis ready"

down: ## Stop Postgres + Redis
	docker compose down

migrate: ## Apply database migrations to head
	cd $(BACKEND) && .venv/bin/alembic upgrade head

bootstrap: ## Create university root + Super Admin (idempotent; prints initial password once)
	cd $(BACKEND) && .venv/bin/python -m unicore.bootstrap \
		--university-name "$(UNIVERSITY_NAME)" --university-code "$(UNIVERSITY_CODE)" \
		--admin-username "$(ADMIN_USERNAME)" --admin-full-name "$(ADMIN_FULLNAME)"

api: ## Run the FastAPI backend (reload; OTPs/temp passwords print here in dev)
	cd $(BACKEND) && .venv/bin/uvicorn unicore.main:app --reload --port 8000

web: ## Run the Next.js frontend dev server
	cd $(FRONTEND) && npm run dev

test: ## Run the backend test suite (needs `make up` first)
	cd $(BACKEND) && .venv/bin/pytest -q

lint: ## Ruff lint
	cd $(BACKEND) && .venv/bin/ruff check .

type: ## Mypy type check
	cd $(BACKEND) && .venv/bin/mypy

check: lint type test ## Lint + types + tests (what CI runs)

build-web: ## Production build of the frontend (separate dist dir — safe while `make web` runs)
	cd $(FRONTEND) && NEXT_DIST_DIR=.next-build npx next build

db-shell: ## psql into the dev database
	docker compose exec postgres psql -U unicore -d unicore

redis-cli: ## redis-cli into the dev Redis
	docker compose exec redis redis-cli

clean: ## Remove caches and build artifacts (keeps venv and node_modules)
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/.next $(FRONTEND)/.next-build
