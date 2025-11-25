UV ?= uv
UV_CACHE_DIR ?= .uv-cache
VENV ?= .venv
PYTHON := $(VENV)/bin/python3
UNAME_S := $(shell uname -s)

export UV_CACHE_DIR

.PHONY: install dev mosaic lint test clean

install:
	$(UV) venv $(VENV)
	$(UV) pip install --python $(PYTHON) -e .
	@if [ "$(UNAME_S)" = "Darwin" ]; then \
		$(UV) pip install --python $(PYTHON) \
			'pyobjc-core>=10,<11' \
			'pyobjc-framework-Cocoa>=10,<11' \
			'pyobjc-framework-ApplicationServices>=10,<11' \
			'pyobjc-framework-Quartz>=10,<11'; \
	fi

dev: install

mosaic: dev
	PYTHONPATH=. $(PYTHON) examples/run_mosaic.py

lint: install
	$(UV) pip install --python $(PYTHON) 'pylint>=4.0.3,<5'
	$(PYTHON) -m pylint $(shell git ls-files '*.py')

test: install
	$(PYTHON) -m unittest discover -s tests -p "test*.py"

clean:
	rm -rf $(VENV)
