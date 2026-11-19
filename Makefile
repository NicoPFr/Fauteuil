.PHONY: build universal install test
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
	bash -n ytmp4.sh scripts/build-app.sh
