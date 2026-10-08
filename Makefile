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

.PHONY: test-fastapi-min
test-fastapi-min: ## Run the FastAPI tests on the lowest supported FastAPI
	uv run --python 3.11 --isolated --with "fastapi==0.105.0" --with "httpx<0.28" pytest tests/test_fastapi.py -p no:cacheprovider

.PHONY: test-faststream-min
test-faststream-min: ## Run the FastStream tests on the lowest supported FastStream
	uv run --python 3.11 --isolated --with "faststream[nats]==0.6.0" pytest tests/test_faststream.py -p no:cacheprovider

.PHONY: test-litestar-min
test-litestar-min: ## Run the Litestar tests on the lowest supported Litestar
	uv run --python 3.11 --isolated --with "litestar==2.15.0" pytest tests/test_litestar.py -p no:cacheprovider

.PHONY: bench
bench: ## Run the benchmarks, see docs/benchmarks.md
	uv run python benchmarks/run.py

.PHONY: bench-all
bench-all: ## Run the benchmarks on every supported Python version, JSON into docs/benchmarks/
	@mkdir -p docs/benchmarks
	@for v in $(PY_VERSIONS); do \
		echo "==> Python $$v"; \
		uv run --isolated --python $$v python benchmarks/run.py --json docs/benchmarks/py$$v.json || exit 1; \
	done

.PHONY: bench-compare
bench-compare: ## Run the comparison with other DI libraries, JSON into docs/benchmarks/
	@mkdir -p docs/benchmarks
	uv run --group compare python benchmarks/compare.py --json docs/benchmarks/compare-py$$(uv run python -c 'import sys; print("%d.%d" % sys.version_info[:2])').json

.PHONY: check-version
check-version: ## Check that the version is bumped against origin/master, as CI does for a pull request
	git fetch --quiet --tags origin master
	uv run --no-project python scripts/version.py check origin/master

.PHONY: check
check: lint test ## Run everything CI runs

.PHONY: build
build: clean ## Build sdist and wheel into dist/
	uv build

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf build dist *.egg-info htmlcov .coverage coverage.xml .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
