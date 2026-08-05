# Thin POSIX wrapper around scripts/check.py (Phase 0 Sec 0.2).
# The real logic lives in check.py so Windows PowerShell and any Bash-tool
# session run the identical command - this file exists only for `make`
# muscle memory on the Bash tool.

PYTHON ?= python

.PHONY: lint format types arch docs test test-fast frontend eval prompts all

lint:
	$(PYTHON) scripts/check.py lint

format:
	$(PYTHON) scripts/check.py format

types:
	$(PYTHON) scripts/check.py types

arch:
	$(PYTHON) scripts/check.py arch

docs:
	$(PYTHON) scripts/check.py docs

test:
	$(PYTHON) scripts/check.py test

test-fast:
	$(PYTHON) scripts/check.py test-fast

frontend:
	$(PYTHON) scripts/check.py frontend

eval:
	$(PYTHON) scripts/check.py eval

prompts:
	$(PYTHON) scripts/check.py prompts

all:
	$(PYTHON) scripts/check.py all
