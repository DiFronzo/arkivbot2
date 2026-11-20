#!/bin/bash
# Fails if the git index contains credentials, macOS droppings, or build junk.
#
# This repo keeps live OAuth secrets next to the code (.env, user-config.py),
# and .DS_Store files reappear whenever Finder opens a folder, so the ignore
# rules alone are not the guarantee. Run by test-local.sh and CI.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "skipped: not a git work tree"
    exit 0
fi

bad="$(git ls-files \
    | grep -vE '^\.env\.example$' \
    | grep -E '(^|/)(\.env(\..+)?|user-config\.py|user-password\.py|passwordfile|\.DS_Store)$|(^|/)__pycache__/|\.pyc$' \
    || true)"

if [[ -n "$bad" ]]; then
    printf 'tracked files that must never be committed:\n%s\n' "$bad" >&2
    exit 1
fi

echo "ok: $(git ls-files | wc -l | tr -d ' ') tracked files, none secret"
