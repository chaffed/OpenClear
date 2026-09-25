#!/bin/bash
# Run this from a terminal (./run_openclear.sh) to set up and run OpenClear locally.
#
# Unlike macOS's run_openclear.command, most Linux file managers don't run a
# double-clicked .sh file in a terminal by default (behavior varies by distro/desktop
# environment, and usually needs "Allow executing file as program" enabled first in the
# file's properties) -- so this is meant to be launched from a terminal. This wrapper
# does nothing but find python3 and hand off to the real (OS-agnostic) launcher — see
# scripts/launcher.py for everything else.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found on your PATH."
  echo "Install Python 3.11+ (e.g. 'sudo apt install python3 python3-venv') and try again."
  read -r -p "Press Enter to close this window..."
  exit 1
fi

# Debian/Ubuntu split the stdlib "venv" module into a separate package -- without it,
# scripts/launcher.py's venv.create() fails with a distro-specific "ensurepip is not
# available" error that's confusing without this pointer.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "Your python3 is missing the 'venv'/'ensurepip' module."
  echo "Install it first, e.g.: sudo apt install python3-venv"
  read -r -p "Press Enter to close this window..."
  exit 1
fi

exec python3 scripts/launcher.py
