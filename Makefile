PY ?= .venv/bin/python
export PYTHONPATH := .:apps/backend:mcp-servers/alarm-management:mcp-servers/ticketing

.PHONY: help venv install ingest run run-alarm-mcp run-ticketing-mcp test test-unit test-mcp test-rag test-e2e coverage lint format typecheck audit docker-up docker-down clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	python3 -m venv .venv

install: venv ## Install runtime + dev dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

ingest: ## Build / refresh the RAG index from rag/documents
	$(PY) -m rag.ingestion.ingest --docs rag/documents --index rag/index/index.json

run: ## Run the whole stack locally without Docker (GUI on :8080)
	scripts/run_local.sh

run-alarm-mcp: ## Run only the Alarm Management MCP server (needs the alarm API on :8000)
	$(PY) -m alarm_mcp

run-ticketing-mcp: ## Run only the ticketing MCP server (needs the ticketing API on :8100)
	$(PY) -m ticketing_mcp

test: ## Run every test (unit, MCP, RAG, orchestration, e2e)
	$(PY) -m pytest

test-unit: ## Unit tests only
	$(PY) -m pytest tests/unit

test-mcp: ## MCP server + client tests
	$(PY) -m pytest tests/integration/test_mcp_servers.py tests/integration/test_mcp_client.py

test-rag: ## RAG ingestion + retrieval tests
	$(PY) -m pytest rag/tests

test-e2e: ## End-to-end test with every service on real ports
	$(PY) -m pytest tests/e2e

coverage: ## Tests with coverage (HTML in docs/coverage/html)
	$(PY) -m pytest --cov --cov-report=term --cov-report=html:docs/coverage/html --cov-report=xml:docs/coverage/coverage.xml

lint: ## Ruff lint + format check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Auto-format
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## mypy static analysis
	$(PY) -m mypy apps/backend/copilot mcp-servers/alarm-management/alarm_mcp mcp-servers/ticketing/ticketing_mcp connectors common rag simulators

audit: ## Dependency vulnerability scan
	$(PY) -m pip_audit -r requirements.txt

docker-up: ## Build and start everything with Docker Compose
	test -f .env || cp .env.example .env
	docker compose up --build

docker-down:
	docker compose down -v

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage rag/index data
