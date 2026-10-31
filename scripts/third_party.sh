#!/usr/bin/env sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Regenerates docs/compliance/third-party.md and THIRD_PARTY_NOTICES.md (scripts/third_party.py)
# where the locks are installed exactly as the image installs them: in its base image, with both
# hashed locks. Run it after changing a lock; the weekly lock refresh runs it too.
#
#     scripts/third_party.sh            # rewrite the two files
#     scripts/third_party.sh --check    # regenerate and compare: exit 1 if the committed files differ (CI; REVIEW_1_33_PLAN.md K1)
set -eu
cd "$(dirname "$0")/.."
BASE=$(sed -n 's/^FROM \(python:[^ ]*\) AS builder$/\1/p' Dockerfile)
if [ "${1:-}" = "--check" ]; then
  keep=$(mktemp -d)
  cp docs/compliance/third-party.md THIRD_PARTY_NOTICES.md "$keep/"
  trap 'cp "$keep/third-party.md" docs/compliance/third-party.md; cp "$keep/THIRD_PARTY_NOTICES.md" THIRD_PARTY_NOTICES.md; rm -rf "$keep"' EXIT
fi
docker run --rm -v "$PWD:/src" -w /src "$BASE" sh -c '
  pip install -q --root-user-action=ignore --no-deps --require-hashes -r requirements/runtime.txt &&
  pip install -q --root-user-action=ignore --no-deps --require-hashes -r requirements/dev.txt &&
  python scripts/third_party.py'
if [ "${1:-}" = "--check" ]; then
  if ! diff -u "$keep/third-party.md" docs/compliance/third-party.md || ! diff -u "$keep/THIRD_PARTY_NOTICES.md" THIRD_PARTY_NOTICES.md; then
    echo "docs/compliance/third-party.md or THIRD_PARTY_NOTICES.md is stale: run scripts/third_party.sh and commit" >&2
    exit 1
  fi
  echo "third-party inventory and notices are current"
fi
