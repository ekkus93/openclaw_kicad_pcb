#!/usr/bin/env bash
# Prevent regenerable output and local tool state from being committed.
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
    echo "git is required for generated-tree validation." >&2
    exit 1
fi

mapfile -t forbidden < <(
    git ls-files -- \
        'code_review/generated/**' \
        'code_review/archive/**/generated/**' \
        'frontend/node_modules/**' \
        'frontend/coverage/**' \
        'frontend/test-results/**' \
        'frontend/playwright-report/**'
)

if ((${#forbidden[@]})); then
    printf 'Forbidden generated files are tracked:\n' >&2
    printf '  %s\n' "${forbidden[@]}" >&2
    exit 1
fi
