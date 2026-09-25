# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.networks.ach.adapter import AchAdapter
from openclear.networks.registry import register_adapter

register_adapter(AchAdapter())
