PYTHON    := python3
UV        ?= uv
VERSION   := $(shell sed -n 's/.*__version__ = "\(.*\)"/\1/p' src/sk/__init__.py)
PACKAGE   := sidekick-agent

.PHONY: help install install-voice build check publish publish-test test clean

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

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info

version: ## Print current package version (single source of truth)
	@echo "$(PACKAGE) $(VERSION)"