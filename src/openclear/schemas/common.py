# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from pydantic import BaseModel


class BulkRowResultOut(BaseModel):
    index: int
    success: bool
    id: str | None = None
    status: str | None = None
    error: str | None = None


class BulkSubmitResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BulkRowResultOut]
