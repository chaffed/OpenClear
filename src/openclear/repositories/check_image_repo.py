# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.check_image import CheckImage
from openclear.repositories.base import CustomerScopedRepository


class CheckImageRepository(CustomerScopedRepository[CheckImage]):
    model = CheckImage
