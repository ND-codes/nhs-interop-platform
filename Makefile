.DEFAULT_GOAL := help
SHELL := /bin/bash

SERVICES := ingest transform pds-client
PROJECT_ROOT := $(shell pwd)

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: demo
demo: ## Build and start the full local stack
	docker compose up --build -d
	@echo "Waiting for HAPI FHIR to be ready (can take ~45 seconds on first boot)..."
	@until curl -sf http://localhost:8080/fhir/metadata > /dev/null; do sleep 2; done
	@echo "Stack is up."
	@echo "  FHIR:         http://localhost:8080/fhir/Patient"
	@echo "  Ingest:       http://localhost:8000/docs"
	@echo "  Transform:    http://localhost:8001/docs"
	@echo "  PDS:          http://localhost:8002/docs"
	@echo "  Grafana:      http://localhost:3000 (admin/admin)"
	@echo "  Prometheus:   http://localhost:9090"
	@echo "  Alertmanager: http://localhost:9093"
	@echo "  Mail inbox:   http://localhost:8025  (where alerts arrive)"
	@echo "Try:  make walkthrough"

.PHONY: up down logs ps
up: ## Start the stack (no rebuild)
	docker compose up -d
down: ## Stop the stack and remove containers
	docker compose down
logs: ## Tail logs from all services
	docker compose logs -f
ps: ## Show running containers
	docker compose ps

.PHONY: clean
clean: ## Stop stack and remove volumes (destroys Postgres + DLQ data)
	docker compose down -v

.PHONY: break-pds restore-pds break-fhir restore-fhir
break-pds: ## Open the PDS circuit breaker on purpose, to watch PDSCircuitOpen fire
	./scripts/break-pds.sh
restore-pds: ## Start pds-client again and close the breaker
	./scripts/restore-pds.sh
break-fhir: ## Stop HAPI FHIR so messages dead-letter, to watch DLQRateHigh fire
	./scripts/break-fhir.sh
restore-fhir: ## Start HAPI FHIR again
	./scripts/restore-fhir.sh

.PHONY: walkthrough
walkthrough: ## Run the ten-minute tour end to end and fail if any step does not hold
	./scripts/walkthrough.sh

.PHONY: alerts
alerts: ## Check and unit test the Prometheus alert rules (needs promtool)
	promtool check rules observability/prometheus/alerts.yml
	cd observability/prometheus && promtool test rules alerts_test.yml

# cygpath exists only in Git Bash on Windows, where Docker wants a C:/ style
# path and MSYS rewrites a leading /app unless it is doubled. Everywhere else
# $(pwd) and /app are already right. Before this, `make test` and `make lint`
# died with "cygpath: command not found" on macOS and Linux, which is every
# reader who is not me.

.PHONY: test
test: ## Run unit tests for every service in a throwaway container
	@for svc in $(SERVICES); do \
		echo "==> Testing services/$$svc" ; \
		abs=$$(cd services/$$svc && pwd) ; wd=/app ; \
		if command -v cygpath >/dev/null 2>&1 ; then abs=$$(cygpath -m "$$abs") ; wd=//app ; fi ; \
		docker run --rm -v "$$abs":/app -w "$$wd" python:3.12-slim \
			sh -c "pip install -q -r requirements-dev.txt && pytest -q" || exit 1 ; \
	done

.PHONY: integration
integration: ## Run the end-to-end integration test (requires the stack to be up)
	@# PEP 668 lets a distribution mark its base interpreter as externally
	@# managed, and pip then refuses to install into it, with
	@# "error: externally-managed-environment". That is the default on Debian,
	@# Ubuntu and Homebrew Python 3.11 and later, so the previous recipe failed
	@# for most readers. A throwaway virtualenv behaves the same everywhere.
	@# The bin/Scripts fallback is for Git Bash.
	@python3 -m venv .venv-integration
	@VP=.venv-integration/bin/python ; \
	[ -x "$$VP" ] || VP=.venv-integration/Scripts/python.exe ; \
	"$$VP" -m pip install --quiet httpx pytest ; \
	"$$VP" -m pytest -q tests/integration

.PHONY: reconcile
reconcile: ## Hash-compare HL7 input batch against FHIR output batch
	python3 scripts/reconcile.py

.PHONY: lint
lint: ## Lint every Python service
	@for svc in $(SERVICES); do \
		echo "==> Linting services/$$svc" ; \
		abs=$$(cd services/$$svc && pwd) ; wd=/app ; \
		if command -v cygpath >/dev/null 2>&1 ; then abs=$$(cygpath -m "$$abs") ; wd=//app ; fi ; \
		docker run --rm -v "$$abs":/app -w "$$wd" python:3.12-slim \
			sh -c "pip install -q ruff==0.6.9 && ruff check ." || exit 1 ; \
	done

.PHONY: scan
scan: ## Run Trivy against the locally built images
	@for svc in $(SERVICES); do \
		echo "==> Scanning nhs-interop/$$svc:local" ; \
		docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
			aquasec/trivy:latest image --severity HIGH,CRITICAL nhs-interop/$$svc:local || exit 1 ; \
	done

.PHONY: sbom
sbom: ## Generate CycloneDX SBOMs for each image
	@mkdir -p sbom
	@for svc in $(SERVICES); do \
		echo "==> SBOM for nhs-interop/$$svc:local" ; \
		docker run --rm -v /var/run/docker.sock:/var/run/docker.sock anchore/syft:latest \
			nhs-interop/$$svc:local -o cyclonedx-json > sbom/$$svc.cyclonedx.json ; \
	done

.PHONY: tf-plan tf-validate
tf-validate: ## terraform fmt + validate (does not talk to AWS)
	cd infra/terraform && terraform init -backend=false && terraform validate && terraform fmt -check
tf-plan: ## terraform plan (requires AWS credentials)
	cd infra/terraform && terraform init && terraform plan -out=tfplan && terraform show -no-color tfplan > ../../docs/terraform-plan.txt
