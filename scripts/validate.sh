#!/usr/bin/env bash
# validate.sh — Run all local quality gates before committing.
#
# Usage:
#   bash scripts/validate.sh          # run everything
#   bash scripts/validate.sh --fast   # skip coverage (plain pytest)
#
# Requirements: activate your Python virtualenv first (e.g. source .venv/bin/activate).
set -euo pipefail

FAST=0
for arg in "$@"; do
    [[ "$arg" == "--fast" ]] && FAST=1
done

echo "==> ruff: lint check"
ruff check .

echo "==> ruff: format check"
ruff format --check .

echo "==> mypy"
mypy src/kicad_pcb src/kicad_pcb_web

echo "==> pytest (unit tests)"
if [[ $FAST -eq 1 ]]; then
    python -m pytest tests/unit/ -q
else
    python -m pytest tests/unit/ -q --cov=kicad_pcb --cov-report=term-missing
fi

echo ""
echo "All checks passed."
