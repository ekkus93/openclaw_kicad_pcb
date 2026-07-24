#!/usr/bin/env bash
# Run the complete non-KiCad verification suite, including packaging and browser smoke tests.
set -euo pipefail

bash scripts/validate.sh "$@"

echo "==> package build"
rm -rf dist
uv build
python scripts/package_smoke_test.py

if [[ " ${*:-} " != *" --python-only "* ]]; then
    echo "==> Playwright smoke tests"
    npm --prefix frontend run test:e2e
fi

echo
echo "All complete verification checks passed."
