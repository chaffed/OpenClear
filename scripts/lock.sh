#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Writes the hashed lock files (OWASP_PLAN.md OW-D9) from pyproject.toml. One universal lock
# per install profile, valid for every supported Python (3.11+) and platform:
#   requirements/runtime.txt  what the Docker image installs: the base install plus every
#                             runtime extra that needs no system libraries
#   requirements/dev.txt      CI (tests, security gates, the docs site) and local development
# Install with: pip install --require-hashes -r requirements/<file> && pip install --no-deps -e .
#
#   scripts/lock.sh             re-lock, keeping current versions where possible
#   scripts/lock.sh --upgrade   move everything to the newest allowed versions
set -euo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-uv}"
command -v "$UV" >/dev/null || UV=.venv/bin/uv
common=(--universal --python-version 3.11 --generate-hashes --quiet "$@")
"$UV" pip compile pyproject.toml --extra pdf --extra postgres --extra s3 --extra azure --extra redis \
  --extra sms --extra textract --extra azure-di "${common[@]}" -o requirements/runtime.txt
"$UV" pip compile pyproject.toml --extra dev --extra docs --extra pdf --extra postgres "${common[@]}" -o requirements/dev.txt
