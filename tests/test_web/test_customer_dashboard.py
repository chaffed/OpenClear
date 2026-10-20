# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""A customer's dashboard leads with its two jobs (UI_PLAN.md UI-D8), and the account menu says
who's signed in (UI-D6)."""

from tests.test_ml.test_per_customer_ml import _make_exception
from tests.test_web.test_journeys import _customer_user, _sign_in_as


def test_nothing_to_decide_says_so_and_offers_the_upload(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="dash-calm")
    _customer, _account, user = _customer_user(db_session, tenant, "dc")
    _sign_in_as(client, tenant, user.email)
    page = client.get("/ui/?home=1").text
    assert "Nothing to decide" in page and "You're all caught up." in page
    assert "Upload issued checks" in page and "No file sent yet" in page


def test_items_to_decide_lead_the_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="dash-busy")
    _customer, account, user = _customer_user(db_session, tenant, "db")
    _make_exception(db_session, tenant, account, users, "7301", "10.00", "99.00")
    _sign_in_as(client, tenant, user.email)
    page = client.get("/ui/?home=1").text
    assert "1 item to decide" in page and "Decide now" in page


def test_the_account_menu_names_the_person_and_offers_only_what_applies(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="dash-menu")
    _sign_in_as(client, tenant, users["admin"].email)
    page = client.get("/ui/").text
    assert users["admin"].email in page and "account-menu" in page
    assert "Switch organization" not in page  # one organization: nowhere to switch to
    assert "Logged in as" not in page


def test_the_banks_dashboard_shows_what_is_due_soonest(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="dash-bank")
    _make_exception(db_session, tenant, account, users, "7401", "10.00", "99.00")
    _sign_in_as(client, tenant, users["admin"].email)
    page = client.get("/ui/").text
    assert "Due soonest" in page and "Check #7401" in page
