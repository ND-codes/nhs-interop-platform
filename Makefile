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
	@echo "  FHIR:       http://localhost:8080/fhir/Patient"
	@echo "  Ingest:     http://localhost:8000/docs"
	@echo "  Transform:  http://localhost:8001/docs"
	@echo "  PDS:        http://localhost:8002/docs"
	@echo "  Grafana:    http://localhost:3000 (admin/admin)"
	@echo "  Prometheus: http://localhost:9090"
	@echo "Try:  ./scripts/send-adt.sh"

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

.PHONY: test
test: ## Run unit tests for every service in a throwaway container
	@for svc in $(SERVICES); do \
		echo "==> Testing services/$$svc" ; \
		docker run --rm -v "$$(cygpath -m $$(cd services/$$svc && pwd))":/app -w //app python:3.12-slim \
			sh -c "pip install -q -r requirements-dev.txt && pytest -q" || exit 1 ; \
	done

.PHONY: integration
integration: ## Run the end-to-end integration test (requires the stack to be up)
	python3 -m pip install --quiet httpx pytest
	python3 -m pytest -q tests/integration

.PHONY: reconcile
reconcile: ## Hash-compare HL7 input batch against FHIR output batch
	python3 scripts/reconcile.py

.PHONY: lint
lint: ## Lint every Python service
	@for svc in $(SERVICES); do \
		echo "==> Linting services/$$svc" ; \
		docker run --rm -v "$$(cygpath -m $$(cd services/$$svc && pwd))":/app -w //app python:3.12-slim \
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
