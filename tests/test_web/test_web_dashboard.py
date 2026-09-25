# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re
from datetime import date
from decimal import Decimal

from openclear.domain.exception_item import ExceptionStatus
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import issued_item_service, stop_payment_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def test_dashboard_shows_stat_cards_with_correct_counts(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dashboard-stats")

    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="5001", amount=Decimal("100.00"), payee_name="Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=users["admin"].id,
    )
    stop_payment_service.create_stop_payment(
        db_session, tenant.id,
        stop_payment_service.StopPaymentInput(
            account_id=account.id, check_number="9001", amount=None, effective_date=date(2026, 1, 1),
            expiration_date=None, reason=None,
        ),
        created_by_user_id=users["admin"].id,
    )
    # A mismatched amount against the issued item above creates an open exception.
    ingest_paid_item(
        db_session, tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="5001", presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 2)),
    )
    db_session.commit()

    _login(client, tenant.slug, users["admin"].email)
    resp = client.get("/ui/")

    assert resp.status_code == 200

    def card(label):
        match = re.search(r'<div class="stat-value[^"]*">([^<]*)</div>\s*<div class="stat-label">' + label, resp.text)
        assert match, label
        return match.group(1)

    assert card("Exceptions need attention") == "1"
    assert card("Awaiting my approval") == "0"
    assert card("Due within 24 hours or overdue") == "0"
    assert card("Oldest item waiting") == "Under 1 hour"
    assert card("Outstanding issued checks") == "1"  # presented for the wrong amount, so still outstanding
    assert card("Active stop payments") == "1"
    assert 'href="/ui/exceptions/approvals"' in resp.text
    assert '/ui/issued-items?status=outstanding' in resp.text
    assert "quick-links" not in resp.text  # the old link list duplicated the sidebar


def test_dashboard_hides_the_approval_card_from_people_who_cannot_approve(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="dashboard-viewer")
    _login(client, tenant.slug, users["viewer"].email)

    page = client.get("/ui/").text

    assert "Exceptions need attention" in page
    assert "Awaiting my approval" not in page
