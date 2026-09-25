# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.ach_authorization_rule import AchAuthorizationRule
from openclear.repositories.base import CustomerScopedRepository


class AchAuthorizationRepository(CustomerScopedRepository[AchAuthorizationRule]):
    model = AchAuthorizationRule
