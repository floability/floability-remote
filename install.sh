#!/bin/sh

# Install floability-remote into an isolated, user-owned virtual environment.
# This script is intentionally POSIX sh compatible so it can be used with:
#   curl -fsSL https://raw.githubusercontent.com/floability/floability-remote/main/install.sh | sh

set -eu

PROJECT_NAME="floability-remote"
INSTALL_ROOT="${FLOABILITY_REMOTE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/$PROJECT_NAME}"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="${FLOABILITY_REMOTE_BIN_DIR:-$HOME/.local/bin}"
SOURCE="${FLOABILITY_REMOTE_SOURCE:-https://github.com/floability/floability-remote/archive/refs/heads/main.tar.gz}"

say() {
    printf '%s\n' "$*"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

find_python() {
    if [ -n "${FLOABILITY_REMOTE_PYTHON:-}" ]; then
        command -v "$FLOABILITY_REMOTE_PYTHON" 2>/dev/null || return 1
        return 0
    fi

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_python)" || fail "Python 3.9 or newer is required."

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    fail "Python 3.9 or newer is required; found $($PYTHON --version 2>&1)."
fi

say "Installing $PROJECT_NAME with $($PYTHON --version 2>&1)"
mkdir -p "$INSTALL_ROOT" "$BIN_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    say "Creating isolated environment: $VENV_DIR"
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
        fail "Python's venv module is unavailable. Install it (for example, python3-venv on Ubuntu) and try again."
    fi
fi

say "Downloading and installing the latest $PROJECT_NAME"
"$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check \
    --quiet \
    --upgrade pip
"$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check \
    --quiet \
    --upgrade "$SOURCE"

ln -sf "$VENV_DIR/bin/$PROJECT_NAME" "$BIN_DIR/$PROJECT_NAME"

if ! "$BIN_DIR/$PROJECT_NAME" --version >/dev/null 2>&1; then
    fail "Installation finished, but the command failed its verification check."
fi

say "Installed $PROJECT_NAME in $VENV_DIR"

case ":$PATH:" in
    *":$BIN_DIR:"*)
        say "Ready. Try: $PROJECT_NAME --help"
        ;;
    *)
        say "Add the command directory to your PATH once:"
        say "  export PATH=\"$BIN_DIR:\$PATH\""
        say "Then run: $PROJECT_NAME --help"
        ;;
esac

