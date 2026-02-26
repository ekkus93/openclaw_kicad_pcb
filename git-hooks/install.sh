#!/usr/bin/env bash
# Install tracked git hooks into .git/hooks/.
# Run once after cloning: bash git-hooks/install.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_SRC="$REPO_ROOT/git-hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

for hook in "$HOOKS_SRC"/*; do
    name="$(basename "$hook")"
    [[ "$name" == "install.sh" ]] && continue
    dst="$HOOKS_DST/$name"
    ln -sf "$REPO_ROOT/git-hooks/$name" "$dst"
    chmod +x "$dst"
    echo "Installed: .git/hooks/$name -> git-hooks/$name"
done
echo "Done. Hooks active for this clone."
