# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from pydantic import BaseModel


class TenantUserRead(BaseModel):
    """One row per TenantMembership, mirroring web/routers/users.py's list page exactly
    — a user with several memberships in this tenant (bank-wide plus per-customer
    scopes) appears once per membership, matching how access is actually granted."""

    user_id: uuid.UUID
    email: str
    security_group_name: str
    customer_name: str | None  # None = bank-wide membership
    is_active: bool  # the membership's own status, not the underlying identity's
    membership_id: uuid.UUID
    membership_created_at: datetime
    last_login_at: datetime | None
    # Per-account access: a customer membership sees all of the customer's accounts, or only
    # account_ids. Always true for a bank-wide membership.
    all_accounts: bool = True
    account_ids: list[uuid.UUID] = []


class AccountAccessUpdate(BaseModel):
    """`all_accounts`: true for every account of the customer (including ones added later);
    false for just `account_ids` (at least one, all the customer's)."""

    all_accounts: bool
    account_ids: list[uuid.UUID] = []


class AccountAccessRead(BaseModel):
    membership_id: uuid.UUID
    customer_id: uuid.UUID
    all_accounts: bool
    account_ids: list[uuid.UUID]
