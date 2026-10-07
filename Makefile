.DEFAULT_GOAL := help

PY_VERSIONS := 3.11 3.12 3.13 3.14

.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install the package and dev dependencies
	uv sync --locked

.PHONY: lint
lint: ## Run ruff (lint + format check) and mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

.PHONY: format
format: ## Autofix lint issues and format the code
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: ## Run mypy only
	uv run mypy

.PHONY: test
test: ## Run tests
	uv run pytest

.PHONY: cov
cov: ## Run tests with coverage report
	uv run pytest --cov --cov-report=term --cov-report=html

.PHONY: test-all
test-all: ## Run tests on every supported Python version
	@for v in $(PY_VERSIONS); do \
		echo "==> Python $$v"; \
		uv run --isolated --python $$v pytest || exit 1; \
	done

.PHONY: check
check: lint test ## Run everything CI runs

.PHONY: build
build: clean ## Build sdist and wheel into dist/
	uv build

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf build dist *.egg-info htmlcov .coverage coverage.xml .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
