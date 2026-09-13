#!/bin/sh
set -eu

TARGET="${1:-sample-files}"
REMOTE="${REVIEW_REMOTE:-origin}"
HOST="${REVIEW_HOST:-127.0.0.1}"
PORT="${REVIEW_PORT:-8000}"
URL="http://${HOST}:${PORT}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: review-tool-1.sh must be run inside the media-archive-tooling Git repository."
  exit 1
fi

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "ERROR: Git remote '$REMOTE' is not configured."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not installed or not on PATH."
  echo "Install uv, then rerun ./scripts/review-tool-1.sh"
  exit 1
fi

printf '%s\n' "Synchronizing repository before Tool 1 review..."
git fetch --prune "$REMOTE"

BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
if [ -z "$BRANCH" ]; then
  echo "ERROR: Detached HEAD. Check out a normal repository branch before reviewing Tool 1."
  exit 1
fi

UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
if [ -z "$UPSTREAM" ] && git show-ref --verify --quiet "refs/remotes/${REMOTE}/${BRANCH}"; then
  UPSTREAM="${REMOTE}/${BRANCH}"
fi

DIRTY=$(git status --porcelain --untracked-files=normal)
if [ -n "$DIRTY" ]; then
  echo "ERROR: Working tree is not clean. Review helper will not pull over local changes."
  printf '%s\n' "$DIRTY"
  exit 1
fi

if [ -n "$UPSTREAM" ]; then
  LOCAL_HEAD=$(git rev-parse HEAD)
  REMOTE_HEAD=$(git rev-parse "$UPSTREAM")
  MERGE_BASE=$(git merge-base HEAD "$UPSTREAM")

  if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    printf '%s\n' "Repository branch is up to date with $UPSTREAM."
  elif [ "$LOCAL_HEAD" = "$MERGE_BASE" ]; then
    printf '%s\n' "Local branch is behind $UPSTREAM; fast-forwarding..."
    git merge --ff-only "$UPSTREAM"
    printf '%s\n' "Repository updated. Restarting review helper from the new version..."
    exec "$0" "$@"
  elif [ "$REMOTE_HEAD" = "$MERGE_BASE" ]; then
    echo "ERROR: Local branch has unpushed commits. Push or deliberately reconcile them first."
    exit 1
  else
    echo "ERROR: Local branch and $UPSTREAM have diverged. Reconcile them before reviewing Tool 1."
    exit 1
  fi
else
  printf '%s\n' "No upstream is configured for '$BRANCH'; continuing without an automatic pull."
fi

if [ ! -d "$TARGET" ]; then
  echo "ERROR: Review target directory does not exist: $TARGET"
  echo "Usage: ./scripts/review-tool-1.sh [directory]"
  exit 1
fi

TARGET_ABS=$(cd "$TARGET" && pwd -P)
TARGET_KEY=$(printf '%s' "$TARGET_ABS" | cksum | awk '{print $1}')
REVIEW_STATE_DIR="${REVIEW_STATE_DIR:-.renamer/review}"
REVIEW_REGISTRY="${REVIEW_REGISTRY_PATH:-${REVIEW_STATE_DIR}/tool-1-${TARGET_KEY}.db}"
REVIEW_LOG_DIR="${REVIEW_LOG_DIR:-${REVIEW_STATE_DIR}/logs-${TARGET_KEY}}"
mkdir -p "$REVIEW_STATE_DIR" "$REVIEW_LOG_DIR"

printf '%s\n' "Preparing Python environment..."
uv sync --extra dev --frozen

printf '%s\n' "Running Tool 1 safe dry-run on: $TARGET_ABS"
printf '%s\n' "Review registry: $REVIEW_REGISTRY"
uv run media-archive renamer "$TARGET_ABS" --dry-run \
  --registry-path "$REVIEW_REGISTRY" \
  --log-dir "$REVIEW_LOG_DIR"

printf '%s\n' "Starting Tool 1 review portal: $URL"
printf '%s\n' "Press Ctrl-C in this terminal when you are finished reviewing."

if [ "${REVIEW_OPEN_BROWSER:-1}" != "0" ]; then
  (
    sleep 1
    if command -v open >/dev/null 2>&1; then
      open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL"
    fi
  ) >/dev/null 2>&1 &
fi

exec uv run media-archive review \
  --host "$HOST" \
  --port "$PORT" \
  --registry-path "$REVIEW_REGISTRY" \
  --review-root "$TARGET_ABS"
