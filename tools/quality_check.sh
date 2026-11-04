#!/usr/bin/env bash
# Mechanical quality harness: lint + type-check + tests.
# Run before/after edits as the fast gate; the semantic bug-hunt is done by a subagent.
# ruff/mypy print-by-default but do not hard-fail (baseline debt is reduced as code is
# touched — ratchet). pytest is the hard gate and exits non-zero on failure.
set -euo pipefail

PY="${PYTHON:-.venv/bin/python}"
echo "== ruff (lint) =="
"$PY" -m ruff check src tools || true

echo "== mypy (types) =="
"$PY" -m mypy src || true

echo "== pytest (hard gate) =="
"$PY" -m pytest "$@"