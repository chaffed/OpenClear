# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.exception_item import ExceptionItem
from openclear.repositories.base import CustomerScopedRepository


class ExceptionRepository(CustomerScopedRepository[ExceptionItem]):
    model = ExceptionItem
