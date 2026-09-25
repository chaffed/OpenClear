# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.customer import Customer
from openclear.repositories.base import TenantScopedRepository


class CustomerRepository(TenantScopedRepository[Customer]):
    model = Customer
