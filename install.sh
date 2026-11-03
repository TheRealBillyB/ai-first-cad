#!/usr/bin/env bash
# Install AI-First CAD into a .venv folder in this project.
# Double-click install.sh on Linux, or Install AI-First CAD.command on a Mac.
# Running this again is safe, including after this folder is updated.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"
export PYTHONUNBUFFERED=1

MIN_OK='import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
PYTHON=""

interactive() {
  [[ -z "${CI:-}" && -z "${GITHUB_ACTIONS:-}" && -t 0 && -t 1 ]]
}

pause_if_interactive() {
  if ! interactive; then
    return 0
  fi
  read -r -p "Press Enter to close this window." _ || true
}

confirm() {
  if ! interactive; then
    return 1
  fi
  local answer
  read -r -p "$1 [y/N] " answer || return 1
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

python_ok() {
  [[ -n "${1:-}" && -x "$1" ]] || return 1
  "$1" -c "$MIN_OK" >/dev/null 2>&1
}

# PYTHON is the interpreter to use. FALLBACK is a suitable interpreter that
# cannot create a virtual environment yet. install.py can add that support
# on Debian, so a fallback is used only when nothing better was found.
FALLBACK=""

consider() {
  python_ok "$1" || return 1
  if "$1" -c 'import ensurepip' >/dev/null 2>&1; then
    PYTHON=$1
    return 0
  fi
  if [[ -z "$FALLBACK" ]]; then
    FALLBACK=$1
  fi
  return 1
}

find_on_path() {
  local name resolved
  for name in python3.11 python3.12 python3.13 python3.14 python3 python; do
    if command -v "$name" >/dev/null 2>&1; then
      resolved=$(command -v "$name")
      if consider "$resolved"; then
        return 0
      fi
    fi
  done
  return 1
}

known_paths() {
  local ver
  for ver in 3.11 3.12 3.13 3.14; do
    echo "/opt/homebrew/bin/python${ver}"
    echo "/usr/local/bin/python${ver}"
    echo "/Library/Frameworks/Python.framework/Versions/${ver}/bin/python3"
    echo "${HOME:-}/Library/Frameworks/Python.framework/Versions/${ver}/bin/python3"
  done
}

find_python() {
  local candidate
  PYTHON=""
  FALLBACK=""
  if [[ -n "${AICAD_PYTHON:-}" ]]; then
    if python_ok "$AICAD_PYTHON"; then
      PYTHON=$AICAD_PYTHON
      return 0
    fi
    echo "The Python in AICAD_PYTHON is missing or older than 3.11:"
    echo "    ${AICAD_PYTHON}"
    return 2
  fi
  if find_on_path; then
    return 0
  fi
  if [[ -z "${AICAD_EASY_INSTALL_PATH_ONLY:-}" ]]; then
    while IFS= read -r candidate; do
      [[ -n "$candidate" ]] || continue
      if consider "$candidate"; then
        return 0
      fi
    done < <(known_paths)
  fi
  if [[ -n "$FALLBACK" ]]; then
    PYTHON=$FALLBACK
    return 0
  fi
  return 1
}

explain_missing() {
  local system
  echo
  echo "Python 3.11 or newer was not found."
  echo "AI-First CAD needs Python. It is free."
  echo
  echo "Download Python 3.11 from:"
  echo "    https://www.python.org/downloads/"
  echo
  system=$(uname -s 2>/dev/null || echo unknown)
  case "$system" in
    Darwin)
      echo "On a Mac, the python.org installer works. You can also install Python 3.11 with Homebrew:"
      echo "    brew install python@3.11"
      ;;
    Linux)
      echo "On Ubuntu or Debian:"
      echo "    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip"
      echo "If that Python is older than 3.11, install python3.11 or python3.12 and the matching venv package."
      echo
      echo "On Fedora:"
      echo "    sudo dnf install -y python3"
      ;;
  esac
  echo
  echo "Then run this installer again."
}

try_brew() {
  command -v brew >/dev/null 2>&1 || return 1
  echo
  echo "This installer can install Python 3.11 with Homebrew."
  confirm "Install Python 3.11 now?" || return 1
  echo
  echo "Installing Python 3.11. This can take a few minutes."
  brew install python@3.11 || return 1
  local prefix
  prefix=$(brew --prefix python@3.11 2>/dev/null || true)
  if [[ -n "$prefix" ]] && python_ok "$prefix/bin/python3.11"; then
    PYTHON=$prefix/bin/python3.11
    return 0
  fi
  hash -r || true
  find_on_path
}

try_apt() {
  command -v apt-get >/dev/null 2>&1 || return 1
  echo
  echo "This installer can install Python with apt. You may be asked for your password."
  confirm "Install Python now?" || return 1
  echo
  echo "Installing Python. This can take a few minutes."
  if ! sudo apt-get update; then
    echo "Could not update the package list."
    return 1
  fi
  if ! sudo apt-get install -y python3 python3-venv python3-pip; then
    echo "Could not install Python with apt."
    return 1
  fi
  hash -r || true
  if ! find_python; then
    sudo apt-get install -y python3.12 python3.12-venv python3.12-pip \
      || sudo apt-get install -y python3.11 python3.11-venv python3.11-pip \
      || true
    hash -r || true
    find_python || true
  fi
  [[ -n "$PYTHON" ]]
}

try_dnf() {
  command -v dnf >/dev/null 2>&1 || return 1
  echo
  echo "This installer can install Python with dnf. You may be asked for your password."
  confirm "Install Python now?" || return 1
  echo
  echo "Installing Python. This can take a few minutes."
  if ! sudo dnf install -y python3; then
    echo "Could not install Python with dnf."
    return 1
  fi
  hash -r || true
  find_python || true
  [[ -n "$PYTHON" ]]
}

try_install_python() {
  [[ -z "${AICAD_EASY_INSTALL_PATH_ONLY:-}" ]] || return 1
  interactive || return 1
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin)
      try_brew
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        try_apt
      elif command -v dnf >/dev/null 2>&1; then
        try_dnf
      else
        return 1
      fi
      ;;
    *)
      return 1
      ;;
  esac
}

fail() {
  echo
  echo "Installation did not finish."
  echo "The messages above say what went wrong."
  echo "You can run this installer again. Running it again is safe."
  pause_if_interactive
  exit 1
}

echo "AI-First CAD installer"
echo
echo "This installs the app into a .venv folder inside this project."
echo "It does not change the rest of this computer unless you agree to install Python."
echo
echo "Step 1 of 2: Looking for Python 3.11 or newer."

if [[ ! -f "$ROOT/install.py" ]]; then
  echo "install.py is missing. Keep this installer in the project folder."
  fail
fi

status=0
find_python || status=$?
if [[ "$status" -eq 2 ]]; then
  pause_if_interactive
  exit 1
fi
if [[ -z "$PYTHON" ]]; then
  explain_missing
  try_install_python || true
fi
if [[ -z "$PYTHON" ]]; then
  echo
  echo "Python 3.11 or newer is still missing."
  echo "Install it, then run this installer again. Running this installer again is safe."
  pause_if_interactive
  exit 1
fi

version=$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
echo "Using Python ${version}"
echo "    ${PYTHON}"
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.maxsize > 2**32 else 1)'; then
  echo
  echo "This Python is 32-bit. AI-First CAD needs 64-bit Python 3.11 or newer."
  echo "Install it from https://www.python.org/downloads/ and run this installer again."
  fail
fi
if [[ "$(uname -s 2>/dev/null || echo unknown)" == "Darwin" && "$version" != 3.11.* ]]; then
  echo
  echo "On a Mac, Python 3.11 is the easiest choice. The sketch solver has a ready-made package for 3.11."
  echo "This installer will continue with Python ${version}."
  echo "If installation stops while building the sketch solver, install Python 3.11 and run this installer again."
fi

echo
echo "Step 2 of 2: Installing AI-First CAD into .venv ."
echo "This can take several minutes. The geometry library is large."
echo "Leave this window open."
echo

if ! "$PYTHON" "$ROOT/install.py"; then
  fail
fi

echo
echo "Install finished."
echo "Next, double-click the launcher:"
echo "  Mac: Launch AI-First CAD.command"
echo "  Linux: launch.sh"
echo "Leave that window open while you use the app. Close it, or press Ctrl+C, to stop."
echo
echo "Running this installer again is safe. After you update this folder, run it again."
pause_if_interactive
