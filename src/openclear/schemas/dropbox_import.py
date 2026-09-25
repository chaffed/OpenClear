# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ImportedFileResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_slug: str
    customer_number: str | None
    kind_subpath: str
    filename: str
    outcome: Literal["imported", "duplicate", "failed"]
    succeeded_count: int | None
    failed_count: int | None
    error: str | None


class ImportRunResponse(BaseModel):
    results: list[ImportedFileResultRead]
