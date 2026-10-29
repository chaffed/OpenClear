#!/usr/bin/env sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Regenerates docs/compliance/third-party.md and THIRD_PARTY_NOTICES.md (scripts/third_party.py)
# where the locks are installed exactly as the image installs them: in its base image, with both
# hashed locks. Run it after changing a lock; the weekly lock refresh runs it too.
#
#     scripts/third_party.sh
set -eu
cd "$(dirname "$0")/.."
BASE=$(sed -n 's/^FROM \(python:[^ ]*\) AS builder$/\1/p' Dockerfile)
docker run --rm -v "$PWD:/src" -w /src "$BASE" sh -c '
  pip install -q --root-user-action=ignore --no-deps --require-hashes -r requirements/runtime.txt &&
  pip install -q --root-user-action=ignore --no-deps --require-hashes -r requirements/dev.txt &&
  python scripts/third_party.py'
