UV ?= uv
VENV ?= .venv
PYTHON := $(VENV)/bin/python3
UNAME_S := $(shell uname -s)

.PHONY: mosaic dev

dev:
	$(UV) venv $(VENV)
	$(UV) pip install --python $(PYTHON) -e .
	@if [ "$(UNAME_S)" = "Darwin" ]; then \
		$(UV) pip install --python $(PYTHON) \
			'pyobjc-core>=10,<11' \
			'pyobjc-framework-Cocoa>=10,<11' \
			'pyobjc-framework-ApplicationServices>=10,<11' \
			'pyobjc-framework-Quartz>=10,<11'; \
	fi

mosaic: dev
	PYTHONPATH=. $(PYTHON) examples/run_mosaic.py
