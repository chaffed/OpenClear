#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Runs one of the three full test suites in parallel, writing everything (progress, failures,
# timings) to .openclear-run/test-logs/<suite>.log. Follow it live in another terminal:
#
#   tail -f .openclear-run/test-logs/sqlite.log
#
# Usage: scripts/run_tests.sh sqlite|postgres|linux [commit]
#   sqlite    this checkout, in-memory SQLite
#   postgres  a commit (default HEAD) in its own worktree, against the Postgres test container
#             (OPENCLEAR_TEST_DATABASE_URL, default the pospay-pg-test container on port 55432)
#   linux     a commit (default HEAD) in a python:3.11-slim container (Docker or Colima)
# Exit status is pytest's. Extra workers: OPENCLEAR_TEST_WORKERS (default: one per CPU).

set -euo pipefail
suite="${1:?usage: scripts/run_tests.sh sqlite|postgres|linux [commit]}"
commit="${2:-HEAD}"
root="$(cd "$(dirname "$0")/.." && pwd)"
logs="$root/.openclear-run/test-logs"
mkdir -p "$logs"
log="$logs/$suite.log"
workers="${OPENCLEAR_TEST_WORKERS:-auto}"
python="$root/.venv/bin/python"

{
  echo "== $suite suite at $(git -C "$root" rev-parse --short "$commit") ($(date '+%Y-%m-%d %H:%M:%S')), workers: $workers"
} > "$log"

case "$suite" in
  sqlite)
    cd "$root"
    "$python" -m pytest -n "$workers" -p no:cacheprovider -q -ra >> "$log" 2>&1
    ;;
  postgres)
    tree="$HOME/.openclear-wt-tests"
    git -C "$root" worktree remove --force "$tree" >/dev/null 2>&1 || true
    git -C "$root" worktree add -q --detach "$tree" "$commit"
    cd "$tree"
    OPENCLEAR_TEST_DATABASE_URL="${OPENCLEAR_TEST_DATABASE_URL:-postgresql+psycopg://pospay:pospay@127.0.0.1:55432/pospay_test_p0}" \
      PYTHONPATH=src:sdks/python/src "$python" -m pytest -n "$workers" -p no:cacheprovider -q -ra >> "$log" 2>&1
    ;;
  linux)
    export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/default/docker.sock}"
    git -C "$root" archive "$commit" | docker run --rm -i python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce bash -c "
      mkdir /src && cd /src && tar x && apt-get update -qq >/dev/null && apt-get install -y -qq tesseract-ocr >/dev/null 2>&1
      pip install -q --require-hashes -r requirements/dev.txt 2>&1 | grep -v notice | tail -3
      pip install -q --no-deps -e . 2>&1 | grep -v notice | tail -3
      python -m pytest -n $workers -p no:cacheprovider -q -ra" >> "$log" 2>&1
    ;;
  *)
    echo "unknown suite: $suite (sqlite, postgres or linux)" >&2
    exit 2
    ;;
esac
