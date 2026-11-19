.PHONY: build universal install test
build:
	./scripts/build-app.sh
universal:
	./scripts/build-app.sh universal
install:
	./scripts/build-app.sh native "$$HOME/Applications/Fauteuil.app"
test:
	PYTHONDONTWRITEBYTECODE=1 python3 -W ignore::ResourceWarning -m unittest discover -s app/tests -p 'test_*.py'
	node app/tests/test_fullscreen.js
	node app/tests/test_end_screen.js
	@for script in app/static/*.js; do node --check "$$script" || exit; done
	bash -n ytmp4.sh scripts/build-app.sh
