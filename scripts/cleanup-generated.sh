#!/usr/bin/env bash
# List or remove regenerable model-evaluation output from the canonical ignored directory.
set -euo pipefail

MODE="dry-run"
case "${1:-}" in
    "") ;;
    --apply) MODE="apply" ;;
    *)
        echo "Usage: $0 [--apply]" >&2
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET="$REPO_ROOT/code_review/generated"

python3 - "$REPO_ROOT" "$TARGET" "$MODE" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
mode = sys.argv[3]
expected = (repo_root / "code_review" / "generated").resolve()

if target != expected:
    raise SystemExit(f"Refusing unexpected cleanup target: {target}")
if repo_root == target or repo_root not in target.parents:
    raise SystemExit(f"Refusing unsafe cleanup target: {target}")

if not target.exists():
    print(f"No generated evaluation directory exists: {target}")
    raise SystemExit(0)
if not target.is_dir():
    raise SystemExit(f"Expected a directory, found: {target}")

entries = sorted(target.iterdir(), key=lambda path: path.name)
if not entries:
    print(f"Generated evaluation directory is already empty: {target}")
    raise SystemExit(0)

print("Regenerable evaluation output:")
for entry in entries:
    print(f"  {entry.relative_to(repo_root)}")

if mode != "apply":
    print("Dry run only. Re-run with --apply to remove the listed entries.")
    raise SystemExit(0)

for entry in entries:
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()
print(f"Removed {len(entries)} generated entr{'y' if len(entries) == 1 else 'ies'}.")
PY
