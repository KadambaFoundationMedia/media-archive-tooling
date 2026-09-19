#!/bin/sh

set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

show_usage() {
    cat <<'EOF'
Usage:
  ./run-media-archive.sh <file-or-folder> [more targets] [options]
  ./run-media-archive.sh --review-only [portal options]

Safe preview:
  ./run-media-archive.sh "/path/to/media" --dry-run

Live processing (renames eligible files and may update Baserow):
  ./run-media-archive.sh "/path/to/media"

The first run prepares the required private Python environment automatically.
EOF
}

if [ "$#" -eq 0 ]; then
    show_usage
    exit 2
fi

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi

    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

UV_BIN=""
if UV_BIN=$(find_uv); then
    :
else
    if ! command -v curl >/dev/null 2>&1; then
        echo "Unable to prepare the application: curl is required to install the environment manager." >&2
        exit 1
    fi

    echo "First-time setup: installing the environment manager..."
    INSTALLER_FILE=$(mktemp "${TMPDIR:-/tmp}/media-archive-uv-installer.XXXXXX")
    trap 'rm -f "$INSTALLER_FILE"' EXIT HUP INT TERM
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$INSTALLER_FILE"
    sh "$INSTALLER_FILE"
    rm -f "$INSTALLER_FILE"
    trap - EXIT HUP INT TERM

    if ! UV_BIN=$(find_uv); then
        echo "Setup could not find the installed environment manager. Reopen Terminal and try again." >&2
        exit 1
    fi
fi

RUNTIME_ENV="$PROJECT_DIR/.renamer/runtime-venv"
READY_STAMP="$RUNTIME_ENV/.media-archive-ready"
if [ ! -x "$RUNTIME_ENV/bin/media-archive" ] \
    || [ ! -f "$READY_STAMP" ] \
    || [ "uv.lock" -nt "$READY_STAMP" ] \
    || [ "pyproject.toml" -nt "$READY_STAMP" ]; then
    echo "Preparing Media Archive Tooling (first run or project update)..."
    UV_PROJECT_ENVIRONMENT="$RUNTIME_ENV" "$UV_BIN" sync --frozen
    touch "$READY_STAMP"
fi

if [ "${1:-}" = "--review-only" ]; then
    shift
    exec "$RUNTIME_ENV/bin/media-archive" review "$@"
fi

exec "$RUNTIME_ENV/bin/media-archive" run "$@"
