# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.networks.check.adapter import CheckAdapter
from openclear.networks.registry import register_adapter

register_adapter(CheckAdapter())
