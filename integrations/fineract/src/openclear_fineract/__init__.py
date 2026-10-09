# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""A reference Apache Fineract adapter for OpenClear. See README.md."""

from openclear_fineract.adapter import Adapter, Report, Settings, State
from openclear_fineract.fineract import Fineract, FineractError

__all__ = ["Adapter", "Fineract", "FineractError", "Report", "Settings", "State"]
