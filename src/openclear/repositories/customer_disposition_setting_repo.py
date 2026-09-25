# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.customer_disposition_setting import CustomerDispositionSetting
from openclear.repositories.base import TenantScopedRepository


class CustomerDispositionSettingRepository(TenantScopedRepository[CustomerDispositionSetting]):
    model = CustomerDispositionSetting
