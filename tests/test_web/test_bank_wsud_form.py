# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's own Written Statement of Unauthorized Debit form (SIMPLIFY_PLAN.md SP1): written in
Markdown or HTML, or uploaded as a PDF, and shown to its customers."""

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.customer import Customer
from openclear.services import bank_form_service
from tests.test_web.test_web_exceptions import _login

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def _save(client, **data):
    client.get("/ui/settings/wsud-form")
    files = {"pdf": ("our-wsud.pdf", data.pop("pdf"), "application/pdf")} if "pdf" in data else None
    return client.post("/ui/settings/wsud-form", data={"csrf_token": client.cookies.get("csrf_token"), **data}, files=files,
                       follow_redirects=False)


def _customer_user(client, db_session, tenant, users):
    """Sign in as a customer's viewer of the same bank."""
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.services import security_group_service

    customer = Customer(tenant_id=tenant.id, name="Oak Dental", customer_number="OAK")
    db_session.add(customer)
    db_session.flush()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    membership = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == users["viewer"].id,
                                                                  TenantMembership.tenant_id == tenant.id)).scalar_one()
    membership.customer_id, membership.security_group_id = customer.id, group.id
    db_session.commit()
    client.cookies.clear()
    _login(client, tenant.slug, users["viewer"].email)


def test_a_markdown_form_shown_to_customers(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-md")
    _login(client, tenant.slug, users["admin"].email)
    assert _save(client, format="markdown", body="## Our statement\n\nSign and fax to **555-0100**.").status_code == 303
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "bank_form.update")).scalars().first()
    _customer_user(client, db_session, tenant, users)
    page = client.get("/ui/wsud").text
    assert "<h2>Our statement</h2>" in page and "<strong>555-0100</strong>" in page
    assert 'href="/ui/wsud"' in page  # in the navigation


def test_html_is_cleaned(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-html")
    _login(client, tenant.slug, users["admin"].email)
    _save(client, format="html", body='<p onclick="x()">Return it to us.</p><script>alert(1)</script><form><input name=a></form>')
    page = client.get("/ui/wsud").text
    assert "Return it to us." in page
    assert "<script>alert(1)" not in page and "onclick" not in page and "<input name" not in page


def test_a_pdf_form_downloads_as_a_pdf(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-pdf")
    _login(client, tenant.slug, users["admin"].email)
    assert _save(client, format="pdf", body="Print, sign, return.", pdf=PDF).status_code == 303
    _customer_user(client, db_session, tenant, users)
    assert "Download the form (PDF)" in client.get("/ui/wsud").text
    download = client.get("/ui/wsud/form.pdf")
    assert download.status_code == 200 and download.content == PDF
    assert download.headers["content-type"] == "application/pdf" and "attachment" in download.headers["content-disposition"]


def test_only_a_real_pdf_is_accepted(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-not-pdf")
    _login(client, tenant.slug, users["admin"].email)
    refused = _save(client, format="pdf", pdf=b"<html>not a pdf</html>")
    assert refused.status_code == 422 and "isn&#39;t a PDF" in refused.text
    assert bank_form_service.get(db_session, tenant.id) is None


def test_without_a_form_customers_are_told_to_contact_the_bank(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="wsud-none")
    _customer_user(client, db_session, tenant, users)
    page = client.get("/ui/wsud").text
    assert "contact" in page and "Download the form" not in page
    assert client.get("/ui/wsud/form.pdf").status_code == 404


def test_the_control_warns_while_consumer_debits_are_returned_without_a_form(db_session, tenant_factory):
    from datetime import date
    from decimal import Decimal

    from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction, AchTransactionSource, AchTransactionType
    from openclear.services import control_checks

    tenant, account, users = tenant_factory.make(slug="wsud-check")
    assert control_checks.wsud_form(db_session, tenant.id).status == control_checks.PASS  # nothing returned yet
    db_session.add(AchTransaction(tenant_id=tenant.id, account_id=account.id, originator_id="1234567890", originator_name="Gym",
                                  amount=Decimal("49.00"), transaction_type=AchTransactionType.DEBIT, sec_code="PPD",
                                  trace_number="091000010000001", effective_date=date.today(), source=AchTransactionSource.API,
                                  settlement_status=AchSettlementStatus.RETURNED))
    db_session.commit()
    assert control_checks.wsud_form(db_session, tenant.id).status == control_checks.WARN
    bank_form_service.save(db_session, tenant.id, format="markdown", body="Call us.", user_id=users["admin"].id)
    db_session.commit()
    assert control_checks.wsud_form(db_session, tenant.id).status == control_checks.PASS
