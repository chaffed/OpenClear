# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.ach_transaction import AchTransaction
from openclear.repositories.base import CustomerScopedRepository


class AchTransactionRepository(CustomerScopedRepository[AchTransaction]):
    model = AchTransaction
