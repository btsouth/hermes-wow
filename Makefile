# hermes-wow development tasks.
#
# `make check` is the gate: five offline suites, no game client, no network. If it
# passes, the addon loads, the two halves agree on the wire format, and the
# release lint is clean. It is also what CI runs.

SHELL := /bin/bash

# The bridge talks to the desktop app over a websocket, which needs the Hermes
# venv's packages; the offline suites do not, so they fall back to system python.
HERMES_VENV ?= $(HOME)/.hermes/hermes-agent/venv/bin/python
PY := $(shell test -x "$(HERMES_VENV)" && echo $(HERMES_VENV) || echo python3)
LUA ?= lua5.1
LUAC ?= luac5.1
ADDON ?= /mnt/data/Games/World of Warcraft/_classic_beta_/Interface/AddOns

.DEFAULT_GOAL := help

.PHONY: help check check-lua check-python install publish package icon banner interface preview lint clean

help: ## Show the targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

check: check-lua check-python ## Run every gate (what CI runs)

check-lua: ## The addon under a stub client API
	$(LUA) tests/wow_stub.lua

check-python: ## Roster, wire format, hostile input, hosts, lint, package, notifications
	$(PY) tests/roster_test.py
	$(PY) tests/roundtrip.py
	$(PY) tests/hostile_test.py
	$(PY) tests/hosts_test.py
	$(PY) tests/addon_checks.py
	$(PY) tests/package_test.py
	$(PY) tests/preview_test.py
	$(PY) tests/notify_test.py

lint: ## Compile every Lua file without running anything
	@for file in addon/HermesAI/*.lua; do $(LUAC) -p $$file || exit 1; done
	@echo "all Lua files compile"

install: ## Copy the addon into the client and publish a snapshot
	./bin/hermes-wow wow install --force
	./bin/hermes-wow wow publish

publish: ## Refresh the snapshot in the client without touching the code
	./bin/hermes-wow wow publish

package: ## Build the release zip into dist/ (refuses an unrecorded or unempty release)
	$(PY) scripts/package_addon.py

icon: ## Turn generated art into the addon icon (make icon MASTER=~/Downloads/emblem.png)
	@test -n "$(MASTER)" || { echo "usage: make icon MASTER=path/to/emblem.png  (512px or larger, transparent)"; exit 1; }
	$(PY) scripts/make_icon.py "$(MASTER)"

banner: ## Compose the store-page banner: emblem + a rendered panel + wordmark
	$(PY) scripts/make_banner.py

interface: ## Move the interface pin to a new client build (make interface IFACE=16002)
	@test -n "$(IFACE)" || { echo "usage: make interface IFACE=16002  (from /run print(GetBuildInfo()) in game)"; exit 1; }
	$(PY) scripts/bump_interface.py "$(IFACE)"

preview: ## Render the panel against the live roster into docs/panel-preview.html
	$(PY) scripts/panel_preview.py

clean: ## Remove caches and generated previews
	rm -rf __pycache__ wowmode/__pycache__ tests/__pycache__ .pytest_cache
