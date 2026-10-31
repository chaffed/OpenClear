# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom-field values on many records at once (plans/CUSTOM_FIELDS_PLAN.md CF5, CF-D10): the update
file, the drop folder and the batch API. They update existing records only, check each value
against its field, keep each row's values before and after, and are audited once."""

import os
import time
from datetime import datetime, timezone

from sqlalchemy import select

from openclear.auth.permissions import CUSTOMER_SCOPE_MASKED_PERMISSIONS, DEFAULT_SECURITY_GROUPS
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind, BulkUploadRowResult
from openclear.services import account_service, custom_field_service as cf, dropbox_import_service, security_group_service, user_service
from openclear.services.custom_field_service import FieldInput
from tests.conftest import TenantFactory, login_headers
from tests.items import _make_customer_with_account
from tests.test_web.test_staff_and_operator import _login


def _define(db_session, tenant, owner, key, **kw):
    data = {"label": key.replace("_", " ").capitalize(), "field_type": "text"}
    data.update(kw)
    definition = cf.create_field(db_session, tenant.id, owner, FieldInput(key=key, **data), user_id=None)
    db_session.commit()
    return definition


def _accounts(db_session, tenant, *numbers):
    created = {n: account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number=n, name=f"Acct {n}"))
               for n in numbers}
    db_session.commit()
    return created


def _upload(client, owner, content: bytes, **data):
    return client.post(f"/ui/custom-fields/{owner}/upload", data={"csrf_token": client.cookies.get("csrf_token"), **data},
                       files={"upload_file": ("fields.csv", content, "text/csv")})


def _audits(db_session, tenant):
    return list(db_session.execute(select(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "custom_field.values.imported")).scalars())


def test_an_update_file_sets_values_on_existing_accounts_and_shows_before_and_after(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-file")
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "account", "opened", field_type="date")
    accounts = _accounts(db_session, tenant, "7001", "7002", "7003")
    cf.set_values(db_session, tenant.id, "account", accounts["7001"].id, {"officer_code": "JS", "opened": "2025-01-01"}, user_id=None)
    cf.set_values(db_session, tenant.id, "account", accounts["7003"].id, {"officer_code": "AB"}, user_id=None)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    assert "Update custom fields" in client.get("/ui/accounts").text
    form = client.get("/ui/custom-fields/accounts/upload")
    assert form.status_code == 200 and "cf_officer_code" in form.text
    assert client.get("/ui/custom-fields/accounts/template.csv").text.strip() == "account_number,cf_officer_code,cf_opened"

    page = _upload(client, "accounts", b"account_number,cf_officer_code,cf_opened\n"
                                        b"7001,MK,\n"            # changes one value; the blank date is left alone
                                        b"7002,=HYPERLINK(1),2026-03-04\n"
                                        b"7003,AB,\n"            # already so: unchanged
                                        b"9999,ZZ,\n"            # no such account: never created
                                        b"7002,QQ,soon\n")       # a bad date: the whole row fails
    assert page.status_code == 200
    assert "3 of 5 succeeded" in page.text and "2 accounts changed; 1 already had these values" in page.text
    assert "What changed" in page.text and "An update can't be backed out" in page.text

    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "account", accounts["7001"].id) == {"officer_code": "MK", "opened": "2025-01-01"}
    assert cf.values_of(db_session, tenant.id, "account", accounts["7002"].id) == {"officer_code": "=HYPERLINK(1)", "opened": "2026-03-04"}
    assert account_service.get_account_by_number(db_session, tenant.id, "9999") is None

    upload = db_session.execute(select(BulkUploadFile).where(BulkUploadFile.tenant_id == tenant.id)).scalar_one()
    assert upload.kind == BulkUploadKind.ACCOUNT_FIELDS and (upload.succeeded_count, upload.failed_count) == (3, 2)
    results = {r.row_index: r for r in db_session.execute(select(BulkUploadRowResult).where(
        BulkUploadRowResult.bulk_upload_file_id == upload.id)).scalars()}
    assert results[0].changes == {"before": {"officer_code": "JS"}, "after": {"officer_code": "MK"}}
    assert results[2].note == "Unchanged" and results[2].changes is None
    assert "No account 9999" in results[3].error and "YYYY-MM-DD" in results[4].error
    [audit] = _audits(db_session, tenant)  # one entry for the file, not one per row
    assert audit.action == "custom_field.values.imported" and audit.metadata_json["changed"] == 2

    csv_text = client.get(f"/ui/bulk-uploads/{upload.id}/results.csv").text
    assert "officer_code: JS → MK" in csv_text
    assert "officer_code: (blank) → =HYPERLINK(1)" in csv_text  # starts with the key, so a spreadsheet won't run it
    assert "/fix" not in client.get(f"/ui/bulk-uploads/{upload.id}").text  # updates aren't fixed row by row


def test_blank_cells_clear_values_only_when_asked(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-blank")
    _define(db_session, tenant, "account", "officer_code")
    accounts = _accounts(db_session, tenant, "7101")
    cf.set_values(db_session, tenant.id, "account", accounts["7101"].id, {"officer_code": "JS"}, user_id=None)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    assert "1 of 1 succeeded" not in _upload(client, "accounts", b"account_number,cf_officer_code\n7101,\n").text  # nothing to update
    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "account", accounts["7101"].id) == {"officer_code": "JS"}
    assert "1 of 1 succeeded" in _upload(client, "accounts", b"account_number,cf_officer_code\n7101,\n", blank_clears="true").text
    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "account", accounts["7101"].id) == {"officer_code": None}


def test_na_in_an_update_file_is_text_not_a_blank_cell(client, db_session, tenant_factory):
    """REVIEW_1_33_PLAN.md C6: an officer code of "NA" is a value; pandas would have read it as nothing."""
    tenant, _house, users = tenant_factory.make(slug="cfb-na")
    _define(db_session, tenant, "account", "officer_code")
    accounts = _accounts(db_session, tenant, "7201", "7202")
    _login(client, tenant.slug, users["admin"].email)
    assert "2 of 2 succeeded" in _upload(client, "accounts", b"account_number,cf_officer_code\n7201,NA\n7202,null\n").text
    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "account", accounts["7201"].id) == {"officer_code": "NA"}
    assert cf.values_of(db_session, tenant.id, "account", accounts["7202"].id) == {"officer_code": "null"}


def test_a_file_that_doesnt_name_records_or_fields_is_refused_whole(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-refuse")
    _define(db_session, tenant, "account", "officer_code")
    _login(client, tenant.slug, users["admin"].email)
    no_key = _upload(client, "accounts", b"number,cf_officer_code\n1,JS\n")
    assert no_key.status_code == 422 and "account_number or external_account_id column" in no_key.text
    no_fields = _upload(client, "accounts", b"account_number,officer_code\n1,JS\n")
    assert no_fields.status_code == 422 and "No custom-field columns" in no_fields.text
    # A mistyped header fails the file, not every row (REVIEW_1_33_PLAN.md C5); so does an archived field's.
    typo = _upload(client, "accounts", b"account_number,cf_oficer_code\n1,JS\n")
    assert typo.status_code == 422 and "No such field: cf_oficer_code" in typo.text
    gone = _define(db_session, tenant, "account", "old_code")
    cf.archive_field(db_session, tenant.id, gone.id)
    db_session.commit()
    archived = _upload(client, "accounts", b"account_number,cf_old_code\n1,JS\n")
    assert archived.status_code == 422 and "Archived, so not written: cf_old_code" in archived.text
    assert client.get("/ui/custom-fields/branches/upload").status_code == 404


def test_customer_fields_by_file_refuse_a_customer_whose_service_ended(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-cust")
    _define(db_session, tenant, "customer", "tier", field_type="choice", options=["Gold", "Silver"])
    active, _a = _make_customer_with_account(db_session, tenant, "CFB-1")
    ended, _b = _make_customer_with_account(db_session, tenant, "CFB-2")
    active.external_customer_id = "EXT-1"
    ended.service_ended_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    assert "Update custom fields" in client.get("/ui/customers").text
    page = _upload(client, "customers", f"external_customer_id,customer_number,cf_tier\nEXT-1,,Gold\n,{ended.customer_number},Silver\n".encode())
    assert "1 of 2 succeeded" in page.text and "Positive pay ended" in page.text
    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "customer", active.id) == {"tier": "Gold"}
    assert cf.values_of(db_session, tenant.id, "customer", ended.id) == {}


def test_only_the_banks_staff_with_the_permission_update_fields(client, db_session, tenant_factory):
    assert "custom_field:write" in CUSTOMER_SCOPE_MASKED_PERMISSIONS
    assert "custom_field:write" in DEFAULT_SECURITY_GROUPS["Admin"] and "custom_field:write" not in DEFAULT_SECURITY_GROUPS["Bookkeeper"]
    tenant, _house, users = tenant_factory.make(slug="cfb-perm")
    _define(db_session, tenant, "account", "officer_code")
    _login(client, tenant.slug, users["viewer"].email)
    assert client.get("/ui/custom-fields/accounts/upload").status_code == 403
    assert "Update custom fields" not in client.get("/ui/accounts").text


def _drop(root, slug, subpath, content):
    inbox = root / slug / subpath / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / "fields.csv"
    path.write_bytes(content)
    old = time.time() - 120
    os.utime(path, (old, old))
    return path


def test_the_drop_folder_updates_fields_for_the_bank_only(db_session, tenant_factory, monkeypatch, tmp_path):
    root = tmp_path / "dropbox"
    monkeypatch.setenv("OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR", str(root))
    get_settings.cache_clear()
    try:
        tenant, _house, _users = tenant_factory.make(slug="cfb-drop")
        _define(db_session, tenant, "account", "officer_code")
        customer, account = _make_customer_with_account(db_session, tenant, "CFBD1")
        db_session.commit()
        bank_file = _drop(root, tenant.slug, "fields/accounts", f"account_number,cf_officer_code\n{account.account_number},HR\n".encode())
        customer_file = _drop(root / tenant.slug, customer.customer_number, "fields/accounts",
                              f"account_number,cf_officer_code\n{account.account_number},XX\n".encode())

        results = dropbox_import_service.scan_and_import_tenant(db_session, tenant)
        assert [(r.kind_subpath, r.outcome) for r in results] == [("fields/accounts", "imported")]
        assert not bank_file.exists() and customer_file.exists()  # a customer's folder never updates fields
        db_session.expire_all()
        assert cf.values_of(db_session, tenant.id, "account", account.id) == {"officer_code": "HR"}
        [audit] = _audits(db_session, tenant)
        assert audit.channel == "system" and audit.actor_user_id is None
    finally:
        get_settings.cache_clear()


def test_the_batch_api_updates_existing_records_each_on_its_own(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-api")
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "account", "agg_id", unique=True)
    _define(db_session, tenant, "customer", "tier")
    accounts = _accounts(db_session, tenant, "7201", "7202", "7203")
    accounts["7203"].external_account_id = "EXT-7203"
    cf.set_values(db_session, tenant.id, "account", accounts["7201"].id, {"officer_code": "JS", "agg_id": "G-1"}, user_id=None)
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.post("/api/v1/accounts/custom-fields", headers=headers, json=[
        {"account_number": "7201", "custom_fields": {"officer_code": None}},          # null clears; agg_id left out, left alone
        {"account_number": "7202", "custom_fields": {"agg_id": "G-1"}},               # already on 7201: this one fails alone
        {"external_account_id": "EXT-7203", "custom_fields": {"officer_code": "MK"}},
        {"account_number": "0000", "custom_fields": {"officer_code": "MK"}},
        {"account_number": "7202", "custom_fields": {"nope": "x"}},
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["total"], body["changed"], body["unchanged"], body["failed"]) == (5, 2, 0, 3)
    first, clash, external, missing, unknown = body["results"]
    assert first["record"] == "7201" and first["changes"] == {"before": {"officer_code": "JS"}, "after": {"officer_code": None}}
    assert not clash["success"] and "already on another account" in clash["error"]
    assert external["success"] and external["record"] == "7203"
    assert "No account 0000" in missing["error"] and "No such field: nope" in unknown["error"]
    db_session.expire_all()
    assert cf.values_of(db_session, tenant.id, "account", accounts["7201"].id) == {"officer_code": None, "agg_id": "G-1"}
    assert cf.values_of(db_session, tenant.id, "account", accounts["7203"].id)["officer_code"] == "MK"
    assert client.get(f"/api/v1/bulk-uploads/{body['upload_id']}", headers=headers).json()["kind"] == "account_fields"
    assert len(_audits(db_session, tenant)) == 1

    customer, _acct = _make_customer_with_account(db_session, tenant, "CFBA1")
    db_session.commit()
    done = client.post("/api/v1/customers/custom-fields", headers=headers,
                       json=[{"customer_number": customer.customer_number, "custom_fields": {"tier": "Gold"}}]).json()
    assert done["changed"] == 1 and cf.values_of(db_session, tenant.id, "customer", customer.id) == {"tier": "Gold"}

    too_many = client.post("/api/v1/accounts/custom-fields", headers=headers,
                           json=[{"account_number": "7201", "custom_fields": {}}] * 1001)
    assert too_many.status_code == 422


def test_the_batch_api_is_refused_without_the_permission(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfb-api-perm")
    customer, account = _make_customer_with_account(db_session, tenant, "CFBP1")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="ap@cfb-api-perm.example.com",
                                                      password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    body = [{"account_number": account.account_number, "custom_fields": {}}]
    for email in (users["viewer"].email, person.email):  # no permission; a customer's admin, whose session masks it
        headers = login_headers(client, tenant.slug, email)
        assert client.post("/api/v1/accounts/custom-fields", headers=headers, json=body).status_code == 403


def test_the_control_check_warns_while_a_required_field_is_empty(db_session, tenant_factory):
    from openclear.services import control_checks

    tenant, _house, _users = tenant_factory.make(slug="cfb-check")
    assert control_checks.custom_fields(db_session, tenant.id).status == "PASS"  # nothing required
    _define(db_session, tenant, "account", "officer_code", required=True)
    accounts = _accounts(db_session, tenant, "7301", "7302")
    cf.set_values(db_session, tenant.id, "account", accounts["7301"].id, {"officer_code": "JS"}, user_id=None)
    cf.set_values(db_session, tenant.id, "account", accounts["7302"].id, {"officer_code": "MK"}, user_id=None)
    cf.set_values(db_session, tenant.id, "account", accounts["7302"].id, {"officer_code": None}, user_id=None)  # cleared: a row, no value
    db_session.commit()
    check = control_checks.custom_fields(db_session, tenant.id)
    house_and_cleared = 2  # the tenant's own house account has none either
    assert check.status == "WARN" and check.evidence["accounts_missing"] == house_and_cleared
    assert set(cf.missing_required(db_session, tenant.id, "account")) >= {accounts["7302"].id}
    assert accounts["7301"].id not in cf.missing_required(db_session, tenant.id, "account")
