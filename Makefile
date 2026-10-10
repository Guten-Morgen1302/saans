# Saans build/deploy. On Windows run inside Git Bash; `sam build --use-container`
# runs the build-* targets in a Linux container, so ARM64 wheels are correct.

STACK ?= saans

.PHONY: test deploy smoke

test:
	python -m pytest -q

# SAM calls these (Metadata: BuildMethod: makefile). Only runtime files are shipped.
build-WebhookFunction build-ScheduledFunction:
	mkdir -p "$(ARTIFACTS_DIR)/backtest" "$(ARTIFACTS_DIR)/config"
	cp -r saans "$(ARTIFACTS_DIR)/"
	cp rules.yaml school.yaml "$(ARTIFACTS_DIR)/"
	cp config/stations.yaml config/calendar_2026.yaml "$(ARTIFACTS_DIR)/config/"
	cp backtest/calendar_2025.yaml "$(ARTIFACTS_DIR)/backtest/"
	python -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)" \
		--platform manylinux2014_aarch64 --implementation cp --python-version 3.12 \
		--only-binary=:all: --upgrade
	rm -rf "$(ARTIFACTS_DIR)"/saans/__pycache__

# Deploy, then prove the webhook works end to end (CEO D11 / eng R14).
deploy:
	sam build --use-container
	sam deploy --stack-name $(STACK)
	python scripts/smoke.py --stack $(STACK)

smoke:
	python scripts/smoke.py --stack $(STACK)
