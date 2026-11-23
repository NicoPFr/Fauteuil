.PHONY: build universal install test test-install
build:
	./scripts/build-app.sh
universal:
	./scripts/build-app.sh universal
install:
	./scripts/build-app.sh native "$$HOME/Applications/Fauteuil.app"
test:
	PYTHONPATH=app PYTHONDONTWRITEBYTECODE=1 python3 -W ignore::ResourceWarning -m unittest discover -s tests/python -p 'test_*.py'
	@for test in tests/web/test_*.js; do node "$$test" || exit; done
	@for script in app/web/scripts/*.js; do node --check "$$script" || exit; done
	@for script in ytmp4.sh scripts/build-app.sh scripts/install-runtime.sh; do bash -n "$$script" || exit; done
test-install:
	@set -e; runner=$$(mktemp -t fauteuil-install-test); trap 'rm "$$runner"' EXIT; \
	swiftc -parse-as-library app/macos/ApplicationInstallation.swift tests/macos/ApplicationInstallationTests.swift -o "$$runner"; \
	"$$runner" build/Fauteuil.app
