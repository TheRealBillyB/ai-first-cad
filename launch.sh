#!/usr/bin/env bash
# Start AI-First CAD from the .venv in this folder and open it in the browser.
# Double-click launch.sh on Linux, or Launch AI-First CAD.command on a Mac.
# This window stays open while the app runs. Close it, or press Ctrl+C, to stop.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"
export PYTHONUNBUFFERED=1

interactive() {
  [[ -z "${CI:-}" && -z "${GITHUB_ACTIONS:-}" && -t 0 && -t 1 ]]
}

pause_if_interactive() {
  if ! interactive; then
    return 0
  fi
  read -r -p "Press Enter to close this window." _ || true
}

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "AI-First CAD is not installed in this folder yet."
  echo "Double-click the installer and wait until it finishes."
  echo "  Mac: Install AI-First CAD.command"
  echo "  Linux: install.sh"
  echo "Then start the app again."
  pause_if_interactive
  exit 1
fi

if [[ ! -f "$ROOT/scripts/desktop_launch.py" ]]; then
  echo "The launcher script is missing. Keep this file in the project folder."
  pause_if_interactive
  exit 1
fi

echo "Starting AI-First CAD."
echo "This window stays open while the app runs."
echo "To stop it, close this window or press Ctrl+C."
echo

# Ctrl+C is delivered to the app launcher. Keep this script alive long enough
# to show the closing line instead of disappearing with the signal.
trap 'true' INT
set +e
"$PY" "$ROOT/scripts/desktop_launch.py"
status=$?
set -e
trap - INT

echo
if [[ "$status" -ne 0 ]]; then
  echo "AI-First CAD stopped because of an error."
  echo "The messages above say what went wrong."
else
  echo "AI-First CAD has stopped."
fi
pause_if_interactive
exit "$status"
