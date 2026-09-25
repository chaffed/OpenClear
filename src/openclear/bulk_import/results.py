# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BulkFileRowResult:
    """Shared across every bulk-file import path (issued items, ACH — delimited/Excel/
    NACHA) so the web layer has one result shape to render regardless of source format.
    `row_label` is a human-facing locator: a 1-based row number for tabular files, or
    something like 'entry 3 (trace 123456789012345)' for NACHA, since a raw list index
    means nothing to someone looking at their own spreadsheet or ACH file."""

    row_label: str
    success: bool
    created_id: uuid.UUID | None = None
    error: str | None = None
    # Set only on a successful row that still has something worth telling the user about
    # (e.g. bulk_upload_reversal_service reverting a paid item whose linked issued item
    # had already changed status since) -- every other bulk-import path leaves this None.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class UserBulkRowResult:
    """User bulk-load has a third outcome the other imports don't: a row's email can
    resolve to an identity that already exists in a DIFFERENT tenant, which this app
    never attaches silently (see services/user_service.py) — it's held as
    "needs_confirmation" and shown back to the admin for an explicit follow-up grant
    (web/routers/users.py's bulk-confirm step) rather than folded into success or
    failure."""

    row_label: str
    outcome: Literal["created", "already_member", "needs_confirmation", "failed"]
    email: str | None = None
    security_group_id: uuid.UUID | None = None
    error: str | None = None
    # Set only when outcome == "created" -- the membership row this call produced, so
    # services/bulk_upload_reversal_service.py can track and later back it out.
    membership_id: uuid.UUID | None = None
