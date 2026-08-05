# UniCore — local development
# Quick start:  make install -> make up -> make migrate -> make bootstrap -> make seed
#               -> make api (+ make web)
#
# Runs on macOS, Linux and Windows. On Windows the recipes still run under bash
# (Git for Windows ships one, and anyone with a clone already has it) — only the
# paths differ, because a venv puts its executables in Scripts\ rather than bin/.
# Keeping one set of recipes means a fix on one OS is a fix on all three; two
# parallel sets would drift apart within a month.

ifeq ($(OS),Windows_NT)
  # GNU Make ignores SHELL from the environment on Windows but honours a
  # makefile assignment, so this is what actually decides the interpreter.
  # `bash -c` probes both a cmd.exe parent and an already-POSIX one.
  HAS_BASH := $(findstring ok,$(shell bash -c "echo ok" 2>&1))
  ifeq ($(HAS_BASH),)
    $(error No bash found on PATH. Install Git for Windows (which ships one) or \
run these targets inside WSL. Every recipe here is POSIX shell)
  endif
  SHELL := bash
  PLATFORM := Windows
  # python3 on Windows is often the Microsoft Store stub, which exits without
  # doing anything; `python` is the real interpreter. Override with PYTHON=py
  # if you use the launcher.
  PYTHON ?= python
  VBIN := Scripts
else
  SHELL := /bin/bash
  PLATFORM := $(shell uname -s)
  PYTHON ?= python3
  VBIN := bin
endif

.SHELLFLAGS := -c

BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
# Root-relative (for install) and backend-relative (for targets that cd first,
# because alembic and pytest both need the backend as their working directory).
VENV_BIN := $(VENV)/$(VBIN)
BE_BIN := .venv/$(VBIN)

# Bootstrap defaults — override like: make bootstrap UNIVERSITY_NAME="My University"
UNIVERSITY_NAME ?= Takshashila University
UNIVERSITY_CODE ?= TU
ADMIN_USERNAME  ?= sadmin
ADMIN_FULLNAME  ?= Super Admin
# Org structure seed — override like: make seed CATALOGUE=path/to/other.csv
CATALOGUE       ?= seeds/takshashila_university.csv

.DEFAULT_GOAL := help

.PHONY: help platform install up down migrate bootstrap seed api web test lint type check build-web clean db-shell redis-cli

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUniCore targets:\n\n"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2} END {print ""}' $(MAKEFILE_LIST)
	@echo "  platform: $(PLATFORM) · python: $(PYTHON) · venv bin: $(VBIN)/"
	@echo ""

platform: ## Show what this Makefile detected about your machine
	@echo "OS               : $(PLATFORM)"
	@echo "shell            : $(SHELL)"
	@echo "python launcher  : $(PYTHON)  ($$($(PYTHON) --version 2>&1))"
	@echo "venv executables : $(VENV_BIN)/"
	@test -d $(VENV) && echo "venv             : present" || echo "venv             : missing — run 'make install'"

install: ## Create backend venv + install backend and frontend dependencies
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/python -m pip install -q --upgrade pip
	$(VENV_BIN)/python -m pip install -q -e "$(BACKEND)[dev]"
	cd $(FRONTEND) && npm install --no-audit --no-fund

up: ## Start Postgres + Redis (Docker) and wait until healthy
	docker compose up -d postgres redis
	@until docker compose exec -T postgres pg_isready -U unicore >/dev/null 2>&1; do sleep 1; done
	@echo "postgres + redis ready"

down: ## Stop Postgres + Redis
	docker compose down

migrate: ## Apply database migrations to head
	cd $(BACKEND) && $(BE_BIN)/alembic upgrade head

bootstrap: ## Create university root + Super Admin (idempotent; prints initial password once)
	cd $(BACKEND) && $(BE_BIN)/python -m unicore.bootstrap \
		--university-name "$(UNIVERSITY_NAME)" --university-code "$(UNIVERSITY_CODE)" \
		--admin-username "$(ADMIN_USERNAME)" --admin-full-name "$(ADMIN_FULLNAME)"

seed: ## Load the real org structure (6 Faculties, 13 Schools, 113 Programmes; idempotent)
	cd $(BACKEND) && $(BE_BIN)/python -m unicore.seed \
		--catalogue "$(CATALOGUE)" \
		--university-name "$(UNIVERSITY_NAME)" --university-code "$(UNIVERSITY_CODE)"

api: ## Run the FastAPI backend (reload; OTPs/temp passwords print here in dev)
	cd $(BACKEND) && $(BE_BIN)/uvicorn unicore.main:app --reload --port 8000

web: ## Run the Next.js frontend dev server
	cd $(FRONTEND) && npm run dev

test: ## Run the backend test suite (needs `make up` first)
	cd $(BACKEND) && $(BE_BIN)/pytest -q

lint: ## Ruff lint
	cd $(BACKEND) && $(BE_BIN)/ruff check .

type: ## Mypy type check
	cd $(BACKEND) && $(BE_BIN)/mypy

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
