# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Posted-check file layouts (PAID_ITEM_LAYOUTS_PLAN.md PIL1): reading a core's posted-check
file by a layout (each template, the MICR on-us forms, lines that aren't check debits,
reversals, the trailer check), the builder, and who sees and uses these layouts."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear.bulk_import import layouts
from openclear.bulk_import.fields import RowFieldError
from openclear.domain.import_layout import ImportLayout
from openclear.services import customer_service, import_layout_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers

FIXED = layouts.clean_settings(layouts.PAID_TEMPLATES["fixed_header_trailer"].settings)


def _detail(account, check, cents, code="200", date="10212026"):
    return f"D{account:<12}{check:0>10}{cents:0>12}{date}{code}"


def _trailer(count, cents):
    return f"T{count:0>7}{cents:0>15}"


def _file(*lines) -> bytes:
    return ("\n".join(["H20261021CORE", *lines]) + "\n").encode()


# --- Reading a file ------------------------------------------------------------------------------


@pytest.mark.parametrize("template, content, expected", [
    ("fixed_header_trailer", _file(_detail("1000234561", "1001", "125000"), _trailer(1, "125000")),
     {"account_number": "1000234561", "check_number": "1001", "presented_amount": "1250.00", "presented_date": "2026-10-21"}),
    ("posted_csv", b"Account,Check Number,Amount,Posted Date\n1000234561,1002,1,250.00,2026-10-21\n",
     None),  # an unquoted thousands comma splits the amount: a row error, not a crash
    ("posted_csv", b'Account,Check Number,Amount,Posted Date\n1000234561,1002,"1,250.00",10/21/2026\n',
     {"account_number": "1000234561", "check_number": "1002", "presented_amount": "1250.00", "presented_date": "2026-10-21"}),
    ("micr_onus", b"091000019,1000234561/0001003,99.10,2026-10-21\n",
     {"account_number": "1000234561", "check_number": "1003", "presented_amount": "99.10", "presented_date": "2026-10-21",
      "routing_number": "091000019"}),
])
def test_each_template_reads_its_kind_of_file(template, content, expected):
    settings = layouts.clean_settings(layouts.PAID_TEMPLATES[template].settings)
    [row] = layouts.preview(settings, "posted.txt", content)
    if expected is None:
        assert row.parsed is None and row.error
    else:
        assert row.parsed == expected and row.error is None


@pytest.mark.parametrize("onus, extra, expected", [
    ("1000234561/0001004", {"onus_order": "account_serial"}, ("1000234561", "1004")),
    ("0001004/1000234561", {"onus_order": "serial_account"}, ("1000234561", "1004")),
    ("  1000234561 / 1004 ", {}, ("1000234561", "1004")),
    ("10002345610001004", {"onus_mode": "positions", "onus_account": {"start": 1, "length": 10},
                           "onus_serial": {"start": 11, "length": 7}}, ("1000234561", "1004")),
])
def test_the_onus_field_splits_into_account_and_check_number(onus, extra, expected):
    settings = layouts.clean_settings({**layouts.PAID_TEMPLATES["micr_onus"].settings, **extra})
    parsed = layouts.convert(settings, {"micr_onus": onus, "amount": "5.00", "posted_date": "2026-10-21"})
    assert (parsed["account_number"], parsed["check_number"]) == expected


def test_lines_that_arent_check_debits_and_reversals_are_skipped_not_failed():
    content = _file(_detail("1", "1001", "100"), _detail("1", "0", "500", code="400"), _trailer(2, "600"))
    rows = layouts.preview(FIXED, "p.txt", content)
    assert rows[0].parsed and rows[1].skipped == "transaction code '400' isn't a check debit" and rows[1].error is None
    csv_settings = layouts.clean_settings(layouts.PAID_TEMPLATES["micr_onus"].settings)
    for amount in ("-12.00", "12.00-", "(12.00)"):
        with pytest.raises(layouts.RowSkipped, match="reversal"):
            layouts.convert(csv_settings, {"micr_onus": "1/2", "amount": amount, "posted_date": "2026-10-21"})


def test_a_line_for_another_routing_number_fails():
    settings = layouts.clean_settings({**layouts.PAID_TEMPLATES["micr_onus"].settings, "routing_numbers": "091000019, 021000021"})
    assert layouts.convert(settings, {"routing_number": "021000021", "micr_onus": "1/2", "amount": "1", "posted_date": "2026-10-21"})
    with pytest.raises(RowFieldError) as caught:
        layouts.convert(settings, {"routing_number": "111000025", "micr_onus": "1/2", "amount": "1", "posted_date": "2026-10-21"})
    assert caught.value.field == "routing_number"
    with pytest.raises(layouts.LayoutError, match="9 digits"):
        layouts.clean_settings({**layouts.PAID_TEMPLATES["micr_onus"].settings, "routing_numbers": "12345"})


def test_the_trailer_must_match_the_lines():
    good = _file(_detail("1", "1001", "125000"), _detail("1", "1002", "5000", code="400"), _trailer(2, "130000"))
    check = layouts.check_trailer(FIXED, "p.txt", good)
    assert check.ok and (check.declared_count, check.declared_total) == (2, Decimal("1300.00"))
    assert [label for label, _values in layouts.extract(FIXED, "p.txt", good)] == ["line 1", "line 2"]  # the trailer isn't an item

    short = _file(_detail("1", "1001", "125000"), _trailer(2, "130000"))
    assert layouts.check_trailer(FIXED, "p.txt", short).problem == \
        "The trailer says 2 items totalling 1,300.00; the file has 1 totalling 1,250.00."
    assert "no trailer line" in layouts.check_trailer(FIXED, "p.txt", _file(_detail("1", "1001", "100"))).problem
    assert "isn't a number" in layouts.check_trailer(FIXED, "p.txt", _file(_detail("1", "1001", "100"), "TABCDEFG000000000000100")).problem
    assert layouts.check_trailer(layouts.clean_settings(layouts.PAID_TEMPLATES["posted_csv"].settings), "p.csv", b"x") is None

    delimited = layouts.clean_settings({**layouts.PAID_TEMPLATES["micr_onus"].settings,
                                        "trailer": {"match": "last", "count_column": "2", "total_column": "3"}})
    content = b"091000019,1/1001,10.00,2026-10-21\n091000019,1/1002,2.50-,2026-10-21\nTOTAL,2,7.50\n"
    assert layouts.check_trailer(delimited, "p.csv", content).ok  # a reversal counts against the total
    assert len(layouts.extract(delimited, "p.csv", content)) == 2


@pytest.mark.parametrize("change, message", [
    ({"columns": {"micr_onus": {"position": 2}, "account_number": {"position": 1}, "amount": {"position": 3},
                  "posted_date": {"position": 4}}}, "not both"),
    ({"columns": {"account_number": {"position": 1}, "amount": {"position": 3}, "posted_date": {"position": 4}}},
     "Say where to find: Check number"),
    ({"columns": {**layouts.PAID_TEMPLATES["micr_onus"].settings["columns"], "transaction_code": {"position": 5}}},
     "transaction codes are check debits"),
    ({"trailer": {"match": "prefix"}}, "trailer line starts with"),
    ({"onus_separator": "//"}, "one character"),
])
def test_settings_that_cant_work_are_refused(change, message):
    with pytest.raises(layouts.LayoutError, match=message):
        layouts.clean_settings({**layouts.PAID_TEMPLATES["micr_onus"].settings, **change})


def test_issued_check_settings_saved_before_kinds_still_read_as_issued_checks():
    old = {k: v for k, v in layouts.TEMPLATES["standard_csv"].settings.items() if k != "kind"}
    assert layouts.kind_of(old) == layouts.ISSUED_ITEMS
    assert layouts.clean_settings(old)["kind"] == layouts.ISSUED_ITEMS


# --- The builder and who uses posted-check layouts -------------------------------------------------


def _login(client, slug, email):
    client.cookies.clear()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def test_building_a_posted_check_layout_with_its_trailer(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pil-build")
    csrf = _login(client, tenant.slug, users["admin"].email)
    assert "Posted checks" in client.get("/ui/layouts?kind=paid_items").text
    new = client.get("/ui/layouts/new?kind=paid_items&template=fixed_header_trailer")
    assert new.status_code == 200 and "New posted-check layout" in new.text and 'name="trailer_prefix" value="T"' in new.text

    form = {"csrf_token": csrf, "kind": "paid_items", "name": "Core posted checks", "file_type": "fixed", "line_prefix": "D",
            "amount_format": "implied", "date_format": "MMDDYYYY", "strip_zeros": ["check_number"],
            "debit_values": "200", "trailer_match": "prefix", "trailer_prefix": "T", "trailer_count_start": "2",
            "trailer_count_length": "7", "trailer_total_start": "9", "trailer_total_length": "15"}
    for field, spot in FIXED["positions"].items():
        form[f"start_{field}"], form[f"length_{field}"] = str(spot["start"]), str(spot["length"])
    short = _file(_detail("1000234561", "1001", "125000"), _trailer(2, "130000"))
    preview = client.post("/ui/layouts", data={**form, "action": "preview"}, files={"sample": ("posted.txt", short, "text/plain")})
    assert "would be refused whole" in preview.text and "1250.00" in preview.text

    saved = client.post("/ui/layouts", data={**form, "action": "save", "is_default": "true", "owner": "ignored"}, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"].startswith("/ui/layouts?kind=paid_items")
    layout = db_session.execute(select(ImportLayout).where(ImportLayout.tenant_id == tenant.id)).scalar_one()
    assert (layout.kind, layout.customer_id, layout.is_default) == ("paid_items", None, True)
    assert layout.settings["trailer"] == {"match": "prefix", "prefix": "T", "count_start": 2, "count_length": 7, "total_start": 9,
                                          "total_length": 15}
    assert "Core posted checks" in client.get("/ui/layouts?kind=paid_items").text
    assert "Core posted checks" not in client.get("/ui/layouts?kind=issued_items").text
    assert "posted checks: Core posted checks" in client.get("/ui/settings").text


def test_posted_check_layouts_are_the_banks_staffs_only(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pil-scope")
    layout = import_layout_service.save(db_session, tenant.id, layout=None, name="Core", owner_customer_id=None, kind=layouts.PAID_ITEMS,
                                        raw_settings=layouts.PAID_TEMPLATES["posted_csv"].settings, is_default=True, user_id=None)
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="P-1", name="Pil Co"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.create_user_with_membership(db_session, tenant.id, email="prep@pilco.example", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()

    # A customer's people never see them, by page or API, even holding paid_item:write.
    _login(client, tenant.slug, "prep@pilco.example")
    assert client.get("/ui/layouts?kind=paid_items", follow_redirects=False).status_code == 403
    assert client.get(f"/ui/layouts/{layout.id}", follow_redirects=False).status_code == 404
    assert "Posted checks" not in client.get("/ui/layouts").text
    headers = login_headers(client, tenant.slug, "prep@pilco.example")
    assert client.get("/api/v1/bulk-uploads/layouts?kind=paid_items", headers=headers).json() in ([], {"detail": "Missing permission: paid_item:write"})
    with pytest.raises(import_layout_service.LayoutNotFound):
        import_layout_service.get(db_session, tenant.id, layout.id, customer.id)

    # The bank's staff who may present checks list them by kind; others may not.
    admin = login_headers(client, tenant.slug, users["admin"].email)
    listed = client.get("/api/v1/bulk-uploads/layouts?kind=paid_items", headers=admin).json()
    assert [(row["name"], row["kind"]) for row in listed] == [("Core", "paid_items")]
    assert client.get("/api/v1/bulk-uploads/layouts", headers=admin).json() == []  # issued-check layouts: none
    viewer = login_headers(client, tenant.slug, users["viewer"].email)
    assert client.get("/api/v1/bulk-uploads/layouts?kind=paid_items", headers=viewer).status_code == 403

    # A posted-check layout is always the bank's, whatever owner is asked for, and keeps its kind.
    changed = import_layout_service.save(db_session, tenant.id, layout=layout, name="Core 2", owner_customer_id=customer.id,
                                         raw_settings=layouts.PAID_TEMPLATES["posted_csv"].settings, is_default=True, user_id=None)
    assert (changed.kind, changed.customer_id) == ("paid_items", None)
    assert import_layout_service.upload_options(changed)["layout"]["settings"]["kind"] == "paid_items"


def test_the_sample_can_come_from_an_earlier_posted_check_upload(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="pil-sample")
    csrf = _login(client, tenant.slug, users["admin"].email)
    resp = client.post("/ui/paid-items/bulk", data={"csrf_token": csrf, "format": "tabular"},
                       files={"upload_file": ("posted.csv", f"account_number,check_number,presented_amount,presented_date\n"
                                                            f"{account.account_number},1,2.00,2026-10-21\n".encode(), "text/csv")})
    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", str(resp.url)).group(1)
    page = client.get(f"/ui/layouts/new?kind=paid_items&from_upload={upload_id}")
    assert page.status_code == 200 and "presented_amount" in page.text
    assert client.get(f"/ui/layouts/new?kind=issued_items&from_upload={upload_id}").status_code == 404


# --- Importing with a posted-check layout (PIL2) ---------------------------------------------------


def _core_layout(db_session, tenant, name="Core posted checks", *, is_default=True):
    layout = import_layout_service.save(db_session, tenant.id, layout=None, name=name, owner_customer_id=None, kind=layouts.PAID_ITEMS,
                                        raw_settings=layouts.PAID_TEMPLATES["fixed_header_trailer"].settings, is_default=is_default,
                                        user_id=None)
    db_session.commit()
    return layout


def _paid_items(db_session, tenant):
    from openclear.domain.paid_item import PaidItem

    return {p.check_number: p for p in db_session.execute(select(PaidItem).where(PaidItem.tenant_id == tenant.id)).scalars()}


def test_a_posted_check_file_presents_every_check_and_leaves_out_the_rest(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="pil-import")
    layout = _core_layout(db_session, tenant)
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/paid-items/bulk").text
    assert f'<option value="{layout.id}" selected>Core posted checks (default)</option>' in page

    number = account.account_number
    content = _file(_detail(number, "2001", "12500"), _detail(number, "2002", "500", code="400"), _detail(number, "2003", "9900"),
                    _trailer(3, "22900"))
    resp = client.post("/ui/paid-items/bulk", data={"csrf_token": csrf, "layout_id": str(layout.id)},
                       files={"upload_file": ("posted.txt", content, "text/plain")})
    assert "2 of 2 succeeded" in resp.text and "1 line wasn&#39;t a check to present" in resp.text and ">Core posted checks<" in resp.text
    presented = _paid_items(db_session, tenant)
    assert set(presented) == {"2001", "2003"} and presented["2001"].presented_amount == Decimal("125.00")


def test_a_short_file_is_refused_whole_and_raised(client, db_session, tenant_factory):
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.domain.security_event import SecurityEvent

    tenant, account, users = tenant_factory.make(slug="pil-short")
    layout = _core_layout(db_session, tenant)
    headers = login_headers(client, tenant.slug, users["admin"].email)
    content = _file(_detail(account.account_number, "3001", "12500"), _trailer(2, "25000"))  # the second line never arrived
    resp = client.post("/api/v1/bulk-uploads", headers=headers, data={"kind": "paid_items", "format": "layout", "layout_id": str(layout.id)},
                       files={"file": ("posted.txt", content, "text/plain")})
    assert resp.status_code == 202 and resp.json()["status"] == "rejected"
    assert client.get(resp.json()["status_url"], headers=headers).json()["error"] == \
        "The trailer says 2 items totalling 250.00; the file has 1 totalling 125.00. Nothing in the file was imported."
    assert _paid_items(db_session, tenant) == {}
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                           AuditLogEntry.action == "bulk_upload.refused")).scalar_one()
    assert "posted.txt" in entry.summary
    event = db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "bulk_upload.refused")).scalars().first()
    assert event is not None and event.severity == "high"


def test_customers_cant_upload_with_the_banks_posted_check_layout(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="pil-cust-upload")
    layout = _core_layout(db_session, tenant)
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="P-2", name="Upload Co"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.create_user_with_membership(db_session, tenant.id, email="prep@uploadco.example", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    headers = login_headers(client, tenant.slug, "prep@uploadco.example")
    resp = client.post("/api/v1/bulk-uploads", headers=headers, data={"kind": "paid_items", "format": "layout", "layout_id": str(layout.id)},
                       files={"file": ("posted.txt", b"x", "text/plain")})
    assert resp.status_code in (403, 404)
    csrf = _login(client, tenant.slug, "prep@uploadco.example")
    page = client.get("/ui/paid-items/bulk")
    if page.status_code == 200:
        assert "Core posted checks" not in page.text
        assert client.post("/ui/paid-items/bulk", data={"csrf_token": csrf, "layout_id": str(layout.id)},
                           files={"upload_file": ("p.txt", b"x", "text/plain")}, follow_redirects=False).status_code == 404


def test_the_dropbox_reads_with_the_default_then_each_layouts_folder(db_session, tenant_factory, monkeypatch, tmp_path):
    import os
    import time

    from openclear.config import get_settings
    from openclear.services import dropbox_import_service

    root = tmp_path / "dropbox"
    monkeypatch.setenv("OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR", str(root))
    get_settings.cache_clear()
    try:
        tenant, account, _users = tenant_factory.make(slug="pil-drop")
        _core_layout(db_session, tenant)
        second = import_layout_service.save(db_session, tenant.id, layout=None, name="Second core (CSV)", owner_customer_id=None,
                                            kind=layouts.PAID_ITEMS, raw_settings=layouts.PAID_TEMPLATES["posted_csv"].settings,
                                            is_default=False, user_id=None)
        db_session.commit()
        assert dropbox_import_service.layout_folder(second.name) == "second-core-csv"

        def drop(subpath, name, content):
            inbox = root / tenant.slug / subpath / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / name).write_bytes(content)
            old = time.time() - 120
            os.utime(inbox / name, (old, old))

        number = account.account_number
        drop("checks/tabular", "fixed.txt", _file(_detail(number, "4001", "1000"), _trailer(1, "1000")))
        drop("checks/layout/second-core-csv", "posted.csv",
             f"Account,Check Number,Amount,Posted Date\n{number},4002,20.00,2026-10-21\n".encode())
        drop("checks/tabular", "short.txt", _file(_detail(number, "4003", "1000"), _trailer(2, "2000")))
        results = {r.filename: r for r in dropbox_import_service.scan_and_import_tenant(db_session, tenant)}
        assert results["fixed.txt"].outcome == results["posted.csv"].outcome == "imported"
        assert results["short.txt"].outcome == "failed" and "The trailer says 2 items" in results["short.txt"].error
        from openclear.workers import jobs

        jobs.drain(db_session.get_bind())
        db_session.expire_all()
        assert {"4001", "4002"} <= set(_paid_items(db_session, tenant)) and "4003" not in _paid_items(db_session, tenant)
        assert (root / tenant.slug / "checks/tabular/failed/short.txt").exists()
    finally:
        get_settings.cache_clear()


def test_the_same_check_in_two_files_is_a_duplicate(client, db_session, tenant_factory):
    from openclear.domain.exception_item import ExceptionItem

    from datetime import date

    from openclear.services import issued_item_service

    tenant, account, users = tenant_factory.make(slug="pil-dup")
    layout = _core_layout(db_session, tenant)
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="5001", amount=Decimal("75.00"), payee_name="Acme", issue_date=date(2026, 10, 1)),
        submitted_by_user_id=users["admin"].id)
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)
    for name, check in (("monday.txt", "5001"), ("tuesday.txt", "5001")):
        content = _file(_detail(account.account_number, check, "7500"), _trailer(1, "7500"), "EXTRA" if name == "tuesday.txt" else "")
        resp = client.post("/api/v1/bulk-uploads", headers=headers,
                           data={"kind": "paid_items", "format": "layout", "layout_id": str(layout.id)},
                           files={"file": (name, content, "text/plain")})
        assert resp.status_code == 202, resp.text
    from openclear.workers import jobs

    jobs.drain(db_session.get_bind())
    types = [e.exception_types for e in db_session.execute(select(ExceptionItem).where(ExceptionItem.tenant_id == tenant.id)).scalars()]
    assert len(types) == 1 and "duplicate_paid" in types[0]  # Monday's matched and paid; Tuesday's is the duplicate
