# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.bulk_upload_created_record import BulkUploadCreatedRecord
from openclear.repositories.base import TenantScopedRepository


class BulkUploadCreatedRecordRepository(TenantScopedRepository[BulkUploadCreatedRecord]):
    model = BulkUploadCreatedRecord
