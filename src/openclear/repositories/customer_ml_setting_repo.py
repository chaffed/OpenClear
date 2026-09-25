# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.customer_ml_setting import CustomerMlSetting
from openclear.repositories.base import TenantScopedRepository


class CustomerMlSettingRepository(TenantScopedRepository[CustomerMlSetting]):
    model = CustomerMlSetting
