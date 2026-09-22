#!/bin/sh
# Builder-only GitHub CLI entry point. Do not call `gh` directly from Builder.
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: builder-gh.sh must run inside the repository." >&2
  exit 1
}
TOKEN=$("$ROOT/scripts/builder-github-credential.sh" --token)
GH_TOKEN="$TOKEN" GITHUB_TOKEN="$TOKEN" exec gh "$@"
