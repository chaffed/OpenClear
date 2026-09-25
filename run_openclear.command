#!/bin/bash
# Double-click this file in Finder to set up and run OpenClear locally.
#
# .py files have no default double-click handler on macOS; .command files do (Finder
# runs them in Terminal.app). This wrapper does nothing but find python3 and hand off to
# the real (OS-agnostic) launcher — see scripts/launcher.py for everything else.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found on your PATH."
  echo "Install Python 3.11+ from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close this window..."
  exit 1
fi

exec python3 scripts/launcher.py
