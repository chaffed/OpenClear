# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

from openclear.domain.issued_item import IssuedItem, IssuedItemStatus
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.services import issued_item_service


def _make_items(db_session, tenant, account, admin_user, count: int) -> None:
    for i in range(count):
        issued_item_service.create_issued_item(
            db_session, tenant.id,
            issued_item_service.IssuedItemInput(
                account_id=account.id, check_number=str(1000 + i), amount=Decimal("10.00"), payee_name="Vendor",
                issue_date=date(2026, 1, 1),
            ),
            submitted_by_user_id=admin_user.id,
        )
    db_session.commit()


def test_count_matches_filtered_row_count(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="repo-pagination-count")
    _make_items(db_session, tenant, account, users["admin"], 7)

    repo = IssuedItemRepository(db_session, tenant.id)
    assert repo.count() == 7
    assert repo.count(status=IssuedItemStatus.OUTSTANDING) == 7
    assert repo.count(status=IssuedItemStatus.PAID) == 0


def test_list_limit_and_offset_slice_correctly(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="repo-pagination-slice")
    _make_items(db_session, tenant, account, users["admin"], 10)

    repo = IssuedItemRepository(db_session, tenant.id)
    ordered = repo.list(order_by=IssuedItem.check_number)
    assert [i.check_number for i in ordered] == [str(1000 + i) for i in range(10)]

    page1 = repo.list(order_by=IssuedItem.check_number, limit=4, offset=0)
    page2 = repo.list(order_by=IssuedItem.check_number, limit=4, offset=4)
    page3 = repo.list(order_by=IssuedItem.check_number, limit=4, offset=8)

    assert [i.check_number for i in page1] == ["1000", "1001", "1002", "1003"]
    assert [i.check_number for i in page2] == ["1004", "1005", "1006", "1007"]
    assert [i.check_number for i in page3] == ["1008", "1009"]


def test_list_offset_past_the_end_returns_empty(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="repo-pagination-out-of-range")
    _make_items(db_session, tenant, account, users["admin"], 3)

    repo = IssuedItemRepository(db_session, tenant.id)
    assert repo.list(limit=10, offset=100) == []


def test_list_and_count_without_pagination_args_are_unaffected(db_session, tenant_factory):
    """Backward compatibility: every existing .list(**filters) call site (no limit/
    offset/order_by) must behave exactly as it did before this repo gained pagination
    support."""
    tenant, account, users = tenant_factory.make(slug="repo-pagination-backward-compat")
    _make_items(db_session, tenant, account, users["admin"], 3)

    repo = IssuedItemRepository(db_session, tenant.id)
    assert len(repo.list()) == 3
    assert len(repo.list(status=IssuedItemStatus.OUTSTANDING)) == 3
