.PHONY: build universal test
build:
	./scripts/build-app.sh
universal:
	./scripts/build-app.sh universal
test:
	PYTHONDONTWRITEBYTECODE=1 python3 -W ignore::ResourceWarning -m unittest discover -s app/tests -p 'test_*.py'
	node app/tests/test_fullscreen.js
	node app/tests/test_end_screen.js
	bash -n ytmp4.sh scripts/build-app.sh
