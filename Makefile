PYTHON    := python3
UV        ?= uv
VERSION   := $(shell sed -n 's/.*__version__ = "\(.*\)"/\1/p' src/sk/__init__.py)
PACKAGE   := sidekick-agent
# Pins mirror CI (.github/workflows/ci.yml) and .pre-commit-config.yaml;
# tests/test_ci_config.py asserts those agree — keep this file on the same
# pins when bumping.
RUFF      := uvx ruff@0.16.9
MYPY      := uvx mypy@1.10
# Same baseline path list as the pre-commit mypy-baseline hook and CI lint job.
MYPY_PATHS := src/sk/tools src/sk/agent.py src/sk/router.py src/sk/config.py src/sk/store.py src/sk/slash.py src/sk/tui/ src/sk/cli/ src/sk/model_profiles.py src/sk/anthropic_backend.py src/sk/upgrade.py src/sk/init_wizard.py src/sk/mcp_server.py src/sk/keyring.py src/sk/auth.py src/sk/daemon.py src/sk/brief.py src/sk/jobs.py src/sk/plugins.py src/sk/skills.py src/sk/checkpoints.py src/sk/gitdiff.py src/sk/mcp_client.py src/sk/memory_files.py src/sk/trust.py src/sk/egress.py src/sk/atomic.py src/sk/procutil.py src/sk/fsperm.py src/sk/tokens.py

.PHONY: help install install-voice build check publish publish-test test clean lint typecheck gates

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Install into a global-ish environment (editable, for development)
	$(UV) tool install -e . --force
	@echo "$(PACKAGE) $(VERSION) installed as \`sk\` (dev) — run \`sk version\` to confirm"

install-voice: ## Same as install, with voice (faster-whisper) extras
	$(UV) tool install -e ".[voice]" --force
	@echo "$(PACKAGE) $(VERSION)+voice installed as \`sk\`"

build: clean ## Build sdist + wheel into dist/
	$(UV) build

check: build ## Sanity-check artifacts before uploading
	$(UV) run --with twine twine check dist/*
	@echo "metadata OK: $(PACKAGE)-$(VERSION)"

publish: check ## Upload to PyPI (twine). Requires ~/.pypirc or trusted-publisher CI.
	$(UV) run --with twine twine upload dist/*

publish-test: check ## Upload to Test PyPI (twine requires creds or TEST_PYPI_API_TOKEN)
	$(UV) run --with twine --with "twine~=6.0" twine upload --repository testpypi dist/*

test: ## Run the full test suite (no Ollama needed)
	$(UV) run --with pytest pytest tests -q

lint: ## Lint + format check (mirrors the CI lint job)
	$(RUFF) check src tests
	$(RUFF) format --check src tests

typecheck: ## Typecheck the mypy baseline (same paths as pre-commit/CI)
	$(MYPY) $(MYPY_PATHS)

gates: ## Fast local gates: lint + typecheck + fast pytest subset (mirrors pre-commit)
	$(RUFF) check src tests
	$(RUFF) format --check src tests
	$(MYPY) $(MYPY_PATHS)
	$(UV) run --python 3.12 --with pytest --with hypothesis pytest tests/test_security.py tests/test_properties.py tests/test_daemon.py tests/test_launchd_notify.py -q

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info

version: ## Print current package version (single source of truth)
	@echo "$(PACKAGE) $(VERSION)"