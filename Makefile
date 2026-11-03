PYTHON ?= python3
RELEASE ?= dist/snowflake-engine.zip

.DEFAULT_GOAL := help
.PHONY: help install dev-install test plan release

help:
	@echo "install      Install the engine and its live-operation dependencies"
	@echo "dev-install  Install the engine with test and build tools"
	@echo "test         Run the offline engine test suite"
	@echo "plan         Write an offline install plan using the fictional configuration"
	@echo "release      Create the allowlisted portable source archive"

install:
	$(PYTHON) -m pip install .

dev-install:
	$(PYTHON) -m pip install '.[dev]'

test:
	$(PYTHON) -m pytest tests/engine -q

plan:
	$(PYTHON) -m snowflake_engine.cli install --config configs/engine/example_config.json --output artifacts/engine-install-plan

release:
	$(PYTHON) scripts/engine/package_release.py --output $(RELEASE)
