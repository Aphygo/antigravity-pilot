#!/usr/bin/env bash
set -euo pipefail

AGENT_DESKTOP_VERSION="1.3.1"

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN="${PYTHON:-python3}"
UPGRADE=0

usage() {
  cat <<EOF
Usage: ./scripts/install.sh [--upgrade] [--help]

Installs and configures the antigravity-pilot plugin on macOS and Linux:
  1. Verifies Python 3.9+ (${PYTHON_BIN})
  2. Sets executable permissions on scripts/*.py, bin/pilot-ctl, bin/sandbox-ctl, and installers
  3. Ensures agent-desktop@${AGENT_DESKTOP_VERSION} is installed via npm
  4. On macOS only: compiles src/main.swift -> bin/macos-ctl if swiftc is available (optional accelerator for record & shortcut)
  5. Runs pilot_ctl.py doctor to report environment health

Options:
  --upgrade   Force reinstall/upgrade of agent-desktop@${AGENT_DESKTOP_VERSION}
  -h, --help  Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upgrade)
      UPGRADE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "install.sh: unknown option '$1'" >&2
      usage >&2
      exit 2
      ;;
  esac
done

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
echo "==> Platform: ${OS_NAME} (${ARCH_NAME})"

echo "==> Checking Python 3.9+ (${PYTHON_BIN})..."
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: '${PYTHON_BIN}' was not found on PATH." >&2
  echo "Install Python 3.9+ (e.g. via https://www.python.org/downloads/ or your OS package manager) and re-run." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  DETECTED_PY="$("$PYTHON_BIN" --version 2>&1 || true)"
  echo "Error: Python 3.9+ is required, but found: ${DETECTED_PY}" >&2
  exit 1
fi
echo "    $("$PYTHON_BIN" --version 2>&1) ($(command -v "$PYTHON_BIN"))"

echo "==> Setting executable permissions..."
for pyfile in "$PLUGIN_ROOT"/scripts/*.py; do
  if [[ -f "$pyfile" ]]; then
    chmod +x "$pyfile"
  fi
done
for execfile in \
  "$PLUGIN_ROOT/scripts/install.sh" \
  "$PLUGIN_ROOT/bin/pilot-ctl" \
  "$PLUGIN_ROOT/bin/sandbox-ctl" \
  "$PLUGIN_ROOT/install.sh"; do
  if [[ -f "$execfile" ]]; then
    chmod +x "$execfile"
  fi
done

echo "==> Checking agent-desktop..."
if [[ "$UPGRADE" -eq 1 ]] || ! command -v agent-desktop >/dev/null 2>&1; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "Error: npm is required to install agent-desktop@${AGENT_DESKTOP_VERSION}, but npm was not found on PATH." >&2
    echo "Install Node.js/npm (https://nodejs.org or your system package manager) and re-run ./install.sh." >&2
    exit 1
  fi
  echo "    Installing agent-desktop@${AGENT_DESKTOP_VERSION} globally via npm..."
  npm install -g "agent-desktop@${AGENT_DESKTOP_VERSION}"
fi
echo "    $(agent-desktop --version) ($(command -v agent-desktop))"

if [[ "$OS_NAME" == "Darwin" ]]; then
  echo "==> Checking optional macOS accelerator (swiftc -> bin/macos-ctl)..."
  if command -v swiftc >/dev/null 2>&1 && [[ -f "$PLUGIN_ROOT/src/main.swift" ]]; then
    echo "    $(swiftc --version | head -n 1)"
    mkdir -p "$PLUGIN_ROOT/bin"
    swiftc -O \
      -framework Cocoa \
      -framework ApplicationServices \
      -framework CoreGraphics \
      "$PLUGIN_ROOT/src/main.swift" \
      -o "$PLUGIN_ROOT/bin/macos-ctl"
    chmod +x "$PLUGIN_ROOT/bin/macos-ctl"
    echo "    Built $PLUGIN_ROOT/bin/macos-ctl (optional accelerator for record & shortcut)"
  else
    echo "    swiftc not found (or src/main.swift absent) — skipping optional bin/macos-ctl build."
    echo "    Core pilot-ctl functionality works without it; install Xcode Command Line Tools"
    echo "    ('xcode-select --install') later if you want native macOS record/shortcut support."
  fi
else
  echo "==> Skipping macOS-only swiftc build on ${OS_NAME}."
fi

echo ""
echo "========================================"
echo "  antigravity-pilot Installation Status"
echo "========================================"
echo ""

if [[ -f "$PLUGIN_ROOT/scripts/pilot_ctl.py" ]]; then
  echo "--- pilot_ctl.py doctor ---"
  "$PYTHON_BIN" "$PLUGIN_ROOT/scripts/pilot_ctl.py" doctor || true
else
  echo "Note: $PLUGIN_ROOT/scripts/pilot_ctl.py not found yet; skipping doctor check."
fi

echo ""
echo "Done. Try:"
echo "  \"$PLUGIN_ROOT/bin/pilot-ctl\" doctor"
echo "  \"$PLUGIN_ROOT/bin/sandbox-ctl\" --help"
