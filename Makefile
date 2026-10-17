# Local developer workflow for the load-testing platform (shared kind cluster).
SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON   ?= python3.12
VENV     := .venv
LOADTEST := $(VENV)/bin/loadtest
SAMPLE   := services/sample-api
REGISTRY := localhost:5001
TAG      ?= dev
IMAGE    ?= $(REGISTRY)/sample-api:$(TAG)
CONFIG   ?= $(SAMPLE)/loadtest/loadtest.yaml
# Simply expanded once, so every use in a recipe sees the same timestamp.
ifeq ($(origin RUN_ID),undefined)
RUN_ID   := $(shell date +%Y%m%d-%H%M%S)
endif
# Optional concurrency cap override (LOADTEST_MAX_CONCURRENT_TESTS), e.g. 1 on a laptop.
MAX_CONCURRENT ?=
CONTEXT  := kind-loadtest

.PHONY: help venv test up down image loadtest janitor-now

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv: ## Create .venv (loadtest CLI + dev tools) and the sample service's venv
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e 'engine/cli[dev]'
	$(PYTHON) -m venv $(SAMPLE)/.venv
	$(SAMPLE)/.venv/bin/pip install -q -r $(SAMPLE)/requirements-dev.txt

test: ## Run all unit tests and linters
	$(VENV)/bin/pytest engine -q
	$(VENV)/bin/ruff check engine
	cd $(SAMPLE) && .venv/bin/pytest -q && .venv/bin/ruff check .

up: ## Create the shared kind cluster, registry and platform baseline (idempotent)
	scripts/kind-up.sh

down: ## Delete the kind cluster and the registry
	scripts/kind-down.sh

image: ## Build and push the sample service image (TAG=dev)
	docker build -t $(IMAGE) $(SAMPLE)
	docker push $(IMAGE)

loadtest: ## Run a load test end to end (RUN_ID=<timestamp>, CONFIG=..., TAG=dev, MAX_CONCURRENT=)
	$(if $(MAX_CONCURRENT),LOADTEST_MAX_CONCURRENT_TESTS=$(MAX_CONCURRENT) )$(LOADTEST) run $(CONFIG) --image $(IMAGE) --run-id $(RUN_ID) --out results/$(RUN_ID)

janitor-now: ## Run the janitor CronJob once and show its output
	@job=janitor-manual-$$(date +%s); \
	kubectl --context $(CONTEXT) -n loadtest-system create job --from=cronjob/janitor $$job && \
	kubectl --context $(CONTEXT) -n loadtest-system wait --for=condition=complete job/$$job --timeout=120s && \
	kubectl --context $(CONTEXT) -n loadtest-system logs job/$$job
