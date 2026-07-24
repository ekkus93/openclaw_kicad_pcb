#!/usr/bin/env bash
# Run the ordinary local quality gates used by CI.
set -euo pipefail

FAST=0
PYTHON_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --fast) FAST=1 ;;
        --python-only) PYTHON_ONLY=1 ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $0 [--fast] [--python-only]" >&2
            exit 2
            ;;
    esac
done

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command '$1' is not installed or not on PATH." >&2
        exit 1
    fi
}

require_command uv
require_command dot

echo "==> ruff: lint check"
uv run ruff check .

echo "==> ruff: format check"
uv run ruff format --check .

echo "==> mypy"
uv run mypy src/kicad_pcb src/kicad_pcb_web

echo "==> workflow configuration"
uv run python scripts/check_ci_config.py

echo "==> generated-tree guard"
bash scripts/check-generated-tree.sh

echo "==> pytest (unit + web tests)"
if [[ $FAST -eq 1 ]]; then
    uv run python -m pytest tests/unit tests/web -q
else
    uv run python -m pytest tests/unit tests/web -q --cov --cov-report=term-missing
fi

if [[ $PYTHON_ONLY -eq 0 ]]; then
    require_command node
    require_command npm

    echo "==> frontend: locked install"
    npm --prefix frontend ci

    echo "==> frontend: lint"
    npm --prefix frontend run lint

    echo "==> frontend: unit tests"
    npm --prefix frontend run test:run

    echo "==> frontend: production build"
    npm --prefix frontend run build

    echo "==> frontend: committed bundle check"
    git diff --exit-code -- src/kicad_pcb_web/static/spa
fi

echo
echo "All requested checks passed."
