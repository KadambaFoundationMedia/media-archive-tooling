#!/bin/sh
# Print Builder GitHub credentials only to Git/GitHub CLI subprocesses.
# This file deliberately never logs the token or exports it to the Builder.
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: builder-github-credential.sh must run inside the repository." >&2
  exit 1
}
ENV_FILE="$ROOT/.env"

if [ ! -r "$ENV_FILE" ]; then
  echo "ERROR: .env is missing or unreadable; GITHUB_TOKEN is required for Builder GitHub access." >&2
  exit 1
fi

# GitHub tokens contain no newlines. Keep the value in this short-lived
# subprocess and never source .env, which could execute arbitrary shell text.
TOKEN=$(sed -n 's/^[[:space:]]*GITHUB_TOKEN[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')
case "$TOKEN" in
  ''|\#*)
    echo "ERROR: GITHUB_TOKEN is not set in .env." >&2
    exit 1
    ;;
esac

if [ "${1:-}" = "--token" ]; then
  printf '%s' "$TOKEN"
  exit 0
fi

# Git invokes a credential helper with the operation as its first argument.
# It needs values only for `get`; store/erase are deliberately no-ops.
if [ "${1:-get}" = "get" ]; then
  printf '%s\n' 'username=x-access-token'
  printf '%s\n' "password=$TOKEN"
fi
