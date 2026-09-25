# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.bulk_upload_file import BulkUploadFile
from openclear.repositories.base import CustomerScopedRepository


class BulkUploadFileRepository(CustomerScopedRepository[BulkUploadFile]):
    model = BulkUploadFile
