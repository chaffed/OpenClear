# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.ach_return_reason import AchReturnReason
from openclear.repositories.base import TenantScopedRepository


class AchReturnReasonRepository(TenantScopedRepository[AchReturnReason]):
    model = AchReturnReason
