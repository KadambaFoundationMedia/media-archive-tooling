#!/bin/sh
# Builder-only Git entry point. It avoids SSH and never exposes .env values.
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: builder-git.sh must run inside the repository." >&2
  exit 1
}
. "$ROOT/scripts/builder-github-auth.sh"
exec git "$@"
