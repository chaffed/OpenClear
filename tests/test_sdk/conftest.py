# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The Python SDK is tested from the source tree, so the suite doesn't need it installed."""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "sdks" / "python" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
