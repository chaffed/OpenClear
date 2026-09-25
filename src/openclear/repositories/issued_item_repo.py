# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.issued_item import IssuedItem
from openclear.repositories.base import CustomerScopedRepository


class IssuedItemRepository(CustomerScopedRepository[IssuedItem]):
    model = IssuedItem
