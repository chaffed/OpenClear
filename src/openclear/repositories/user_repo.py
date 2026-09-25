# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.user import User


class UserRepository:
    """User is a global login identity, not a tenant-owned row (see domain/user.py), so
    this deliberately does NOT extend TenantScopedRepository — there is no tenant_id to
    filter by. Access to a given tenant is governed by TenantMembership, not by this
    table; see tenant_membership_repo.py / services/user_service.py for tenant-scoped
    membership queries."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, user_id: uuid.UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.session.execute(select(User).where(User.email == email)).scalar_one_or_none()

    def add(self, user: User) -> User:
        self.session.add(user)
        return user
