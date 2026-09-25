# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.account import Account
from openclear.repositories.base import TenantScopedRepository


class AccountRepository(TenantScopedRepository[Account]):
    model = Account


def test_repository_only_returns_rows_for_its_tenant(db_session, tenant_factory):
    tenant_a, account_a, _ = tenant_factory.make(slug="tenant-a")
    tenant_b, account_b, _ = tenant_factory.make(slug="tenant-b")

    repo_a = AccountRepository(db_session, tenant_a.id)
    repo_b = AccountRepository(db_session, tenant_b.id)

    assert [a.id for a in repo_a.list()] == [account_a.id]
    assert [a.id for a in repo_b.list()] == [account_b.id]

    # Tenant A's repo must not be able to fetch tenant B's row by id, even though it exists.
    assert repo_a.get(account_b.id) is None
    assert repo_b.get(account_a.id) is None


def test_repository_add_stamps_tenant_id_even_if_caller_set_a_different_one(db_session, tenant_factory):
    tenant_a, _account_a, _ = tenant_factory.make(slug="stamp-a")
    tenant_b, _account_b, _ = tenant_factory.make(slug="stamp-b")

    repo_a = AccountRepository(db_session, tenant_a.id)
    # Even if something upstream mistakenly set tenant_b's id, add() must overwrite it.
    rogue = Account(tenant_id=tenant_b.id, account_number="9999", name="Rogue")

    repo_a.add(rogue)
    db_session.commit()

    assert rogue.tenant_id == tenant_a.id
