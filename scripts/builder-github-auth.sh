#!/bin/sh
# Configure the repository once for Builder-safe GitHub HTTPS transport.
# Intended to be sourced by builder-start.sh or builder-git.sh.
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: Builder GitHub setup must run inside the repository." >&2
  return 1 2>/dev/null || exit 1
}
REMOTE="${BUILDER_REMOTE:-origin}"
EXPECTED_URL="https://github.com/KadambaFoundationMedia/media-archive-tooling.git"
CREDENTIAL_HELPER="!$ROOT/scripts/builder-github-credential.sh"

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "ERROR: Git remote '$REMOTE' is not configured." >&2
  return 1 2>/dev/null || exit 1
fi

# The owner explicitly selected HTTPS because the Builder sandbox blocks SSH.
# The helper contains no secret; it reads GITHUB_TOKEN only when Git asks for
# credentials during a remote operation.
if [ "$(git remote get-url "$REMOTE")" != "$EXPECTED_URL" ]; then
  git remote set-url "$REMOTE" "$EXPECTED_URL"
fi
if [ "$(git config --local --get credential.helper 2>/dev/null || true)" != "$CREDENTIAL_HELPER" ]; then
  git config --local credential.helper "$CREDENTIAL_HELPER"
fi
export GIT_TERMINAL_PROMPT=0
