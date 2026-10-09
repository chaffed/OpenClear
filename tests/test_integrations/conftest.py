# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Integrations are tested from the source tree, with the SDK they're built on."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for src in (ROOT / "sdks" / "python" / "src", ROOT / "integrations" / "fineract" / "src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
