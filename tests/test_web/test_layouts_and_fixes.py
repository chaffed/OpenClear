# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Issue-file layouts and fixing failed rows (UX_PLAN.md U1, U2; MASTER_PLAN.md M5): reading
delimited, fixed-width and Excel files by a layout, the builder from a sample, uploads by
the web, the API and the drop folder with a layout, and failed rows fixed in the browser
as audited corrections that backing out reverses."""

import io
import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear.bulk_import import layouts
from openclear.bulk_import.fields import RowFieldError
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bulk_upload_file import BulkUploadCorrection, BulkUploadRowResult
from openclear.domain.import_layout import ImportLayout
from openclear.domain.issued_item import IssuedItem, IssuedItemStatus
from openclear.services import customer_service, import_layout_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def _login(client, slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


FIXED = layouts.clean_settings(layouts.TEMPLATES["fixed_width"].settings)


def _fixed_line(account, check, cents, date, payee="", void=" "):
    return f"{account:<12}{check:0>10}{cents:0>12}{date}{void}{payee:<37}"


# --- The engine ----------------------------------------------------------------------------------


def test_a_fixed_width_file_with_header_and_trailer():
    settings = layouts.clean_settings({**layouts.TEMPLATES["fixed_width"].settings, "line_prefix": "D", "skip_rows": 0})
    settings["positions"] = {k: {"start": v["start"] + 1, "length": v["length"]} for k, v in FIXED["positions"].items()}
    content = "\n".join(["H ACME ISSUE FILE", "D" + _fixed_line("1001", "5001", "12345", "10152026", "Acme Vendor"),
                         "D" + _fixed_line("1001", "5002", "999", "10152026", void="V"), "T 000002"]).encode()
    rows = layouts.preview(settings, "issue.txt", content)
    assert [r.label for r in rows] == ["line 1", "line 2"]
    assert rows[0].parsed == {"account_number": "1001", "check_number": "5001", "void": False, "amount": "123.45",
                              "issue_date": "2026-10-15", "payee_name": "Acme Vendor"}
    assert rows[1].parsed["void"] is True and rows[1].parsed["payee_name"] == "(void)"


def test_delimited_by_position_with_cents_column_and_named_dates():
    settings = layouts.clean_settings({
        "file_type": "delimited", "delimiter": "|", "has_header": False, "date_format": "YYMMDD",
        "columns": {"account_number": {"position": 1}, "check_number": {"position": 2}, "amount": {"position": 3},
                    "amount_cents": {"position": 4}, "issue_date": {"position": 5}, "payee_name": {"position": 6}},
        "strip_zeros": ["account_number"],
    })
    rows = layouts.preview(settings, "f.txt", b"0001001|77|1,250|5|261015|Bob\n0001001|78|12|7|261332|Ann\n")
    assert rows[0].parsed["amount"] == "1250.05" and rows[0].parsed["account_number"] == "1001"
    assert rows[1].error_field == "issue_date" and "YYMMDD" in rows[1].error


def test_excel_by_header_names():
    import pandas as pd

    buffer = io.BytesIO()
    pd.DataFrame({"Acct": ["1001"], "Chk": ["9"], "Amt": ["(10.00)"], "Dated": ["2026-10-01"], "To": ["Zed"]}).to_excel(buffer, index=False)
    settings = layouts.clean_settings({"file_type": "excel", "has_header": True, "columns": {
        "account_number": {"name": "acct"}, "check_number": {"name": "Chk"}, "amount": {"name": "Amt"},
        "issue_date": {"name": "Dated"}, "payee_name": {"name": "To"}}})
    rows = layouts.preview(settings, "f.xlsx", buffer.getvalue())
    assert rows[0].error_field == "amount" and "more than zero" in rows[0].error


def test_amount_and_settings_errors_name_what_is_wrong():
    settings = layouts.clean_settings(layouts.TEMPLATES["standard_csv"].settings)
    with pytest.raises(RowFieldError) as error:
        layouts.convert(settings, {"account_number": "1", "check_number": "2", "amount": "1,2O0.00", "issue_date": "2026-01-01", "payee_name": "x"})
    assert error.value.field == "amount" and "contains a letter" in str(error.value)
    with pytest.raises(layouts.LayoutError, match="Say where to find: Payee"):
        layouts.clean_settings({"file_type": "delimited", "has_header": True, "columns": {
            f: {"name": f} for f in ("account_number", "check_number", "amount", "issue_date")}})
    with pytest.raises(layouts.LayoutError, match="no header row"):
        layouts.clean_settings({"file_type": "delimited", "has_header": False, "columns": {"account_number": {"name": "Acct"}}})
    with pytest.raises(layouts.LayoutError, match="no column named"):
        layouts.extract(settings, "f.csv", b"Acct,Check\n1,2\n")


# --- The builder -----------------------------------------------------------------------------------


SAMPLE = b"Acct,Chk No,Amount,Date,Pay To\n1001,5001,150.00,10/15/2026,Acme Vendor\n1001,5002,1O.00,10/15/2026,Bob\n"


def test_building_a_layout_from_a_sample(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="layout-build")
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.post("/ui/layouts", data={"csrf_token": csrf, "action": "preview", "name": "Acme CSV", "file_type": "delimited",
                                           "delimiter": ",", "has_header": "true"},
                       files={"sample": ("acme.csv", SAMPLE, "text/csv")})
    assert page.status_code == 200
    assert 'name="assign_4"' in page.text and "Pay To" in page.text and "Say where to find" in page.text
    sample_b64 = re.search(r'name="sample_b64" value="([^"]+)"', page.text).group(1)

    mapping = {"assign_0": "account_number", "assign_1": "check_number", "assign_2": "amount", "assign_3": "issue_date",
               "assign_4": "payee_name"}
    preview = client.post("/ui/layouts", data={"csrf_token": csrf, "action": "preview", "name": "Acme CSV", "file_type": "delimited",
                                              "delimiter": ",", "has_header": "true", "sample_b64": sample_b64,
                                              "sample_name": "acme.csv", "date_format": "MM/DD/YYYY", **mapping})
    assert "2026-10-15" in preview.text and "contains a letter" in preview.text and 'class="cell-error"' in preview.text

    saved = client.post("/ui/layouts", data={"csrf_token": csrf, "action": "save", "name": "Acme CSV", "file_type": "delimited",
                                            "delimiter": ",", "has_header": "true", "sample_b64": sample_b64, "sample_name": "acme.csv",
                                            "date_format": "MM/DD/YYYY", "is_default": "true", **mapping}, follow_redirects=False)
    assert saved.status_code == 303
    layout = db_session.execute(select(ImportLayout).where(ImportLayout.tenant_id == tenant.id)).scalar_one()
    assert layout.is_default and layout.settings["columns"]["payee_name"] == {"name": "Pay To"}
    assert "Acme CSV" in client.get("/ui/layouts").text
    assert client.get(f"/ui/layouts/{layout.id}").status_code == 200


def test_a_fixed_width_layout_typed_without_a_sample_and_saving_errors(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="layout-fixed")
    csrf = _login(client, tenant.slug, users["admin"].email)
    form = {"csrf_token": csrf, "action": "save", "name": "Fixed", "file_type": "fixed", "amount_format": "implied",
            "date_format": "MMDDYYYY"}
    missing = client.post("/ui/layouts", data=form)
    assert missing.status_code == 422 and "Say where to find" in missing.text
    for field, spot in FIXED["positions"].items():
        form[f"start_{field}"], form[f"length_{field}"] = str(spot["start"]), str(spot["length"])
    form["void_values"] = "V"
    assert client.post("/ui/layouts", data=form, follow_redirects=False).status_code == 303
    assert db_session.execute(select(ImportLayout).where(ImportLayout.tenant_id == tenant.id)).scalar_one().settings["file_type"] == "fixed"


def test_customers_change_only_their_own_layouts(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="layout-scope")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="L-1", name="Layout Co"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.create_user_with_membership(db_session, tenant.id, email="prep@layoutco.example", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    bank = import_layout_service.save(db_session, tenant.id, layout=None, name="Bank CSV", owner_customer_id=None,
                                      raw_settings=layouts.TEMPLATES["standard_csv"].settings, is_default=True, user_id=None)
    db_session.commit()
    _login(client, tenant.slug, "prep@layoutco.example")
    page = client.get("/ui/layouts").text
    assert "Bank CSV" in page and "Your bank" in page
    assert client.get(f"/ui/layouts/{bank.id}", follow_redirects=False).status_code == 403
    assert 'data-key="layouts"' in client.get("/ui/settings").text
    assert "Bank CSV (default)" in client.get("/ui/issued-items/bulk").text


# --- Uploading with a layout ---------------------------------------------------------------------------


def _upload(client, csrf, content, filename="items.csv", layout_id=""):
    resp = client.post("/ui/issued-items/bulk", data={"csrf_token": csrf, "layout_id": layout_id},
                       files={"upload_file": (filename, content, "text/csv")})
    return resp, re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", resp.url.path if hasattr(resp.url, "path") else str(resp.url)).group(1)


def test_a_fixed_width_upload_creates_and_voids(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="layout-upload")
    layout = import_layout_service.save(db_session, tenant.id, layout=None, name="Fixed", owner_customer_id=None,
                                        raw_settings=FIXED, is_default=False, user_id=None)
    db_session.commit()
    csrf = _login(client, tenant.slug, users["admin"].email)
    content = "\n".join([
        _fixed_line(account.account_number, "7001", "25000", "10152026", "Acme"),
        _fixed_line(account.account_number, "7002", "100", "10152026", "Bob"),
        _fixed_line(account.account_number, "7002", "100", "10152026", void="V"),  # voids the one above
    ]).encode()
    resp, upload_id = _upload(client, csrf, content, "issue.txt", str(layout.id))
    assert "3 of 3 succeeded" in resp.text and ">Fixed<" in resp.text  # the layout it used
    items = {i.check_number: i for i in db_session.execute(select(IssuedItem).where(IssuedItem.tenant_id == tenant.id)).scalars()}
    assert items["7001"].amount == Decimal("250.00") and items["7002"].status == IssuedItemStatus.VOIDED


def test_the_api_and_the_drop_folder_use_layouts(client, db_session, tenant_factory, tmp_path, monkeypatch):
    from openclear.config import get_settings
    from openclear.services import dropbox_import_service

    tenant, account, users = tenant_factory.make(slug="layout-api")
    layout = import_layout_service.save(db_session, tenant.id, layout=None, name="Pipes", owner_customer_id=None, is_default=True, user_id=None,
                                        raw_settings={"file_type": "delimited", "delimiter": "|", "has_header": False, "columns": {
                                            "account_number": {"position": 1}, "check_number": {"position": 2}, "amount": {"position": 3},
                                            "issue_date": {"position": 4}, "payee_name": {"position": 5}}})
    db_session.commit()
    headers = login_headers(client, tenant.slug, users["admin"].email)
    listed = client.get("/api/v1/bulk-uploads/layouts", headers=headers).json()
    assert listed == [{"id": str(layout.id), "kind": "issued_items", "name": "Pipes", "customer_id": None, "is_default": True, "file_type": "delimited"}]
    resp = client.post("/api/v1/bulk-uploads", headers=headers, data={"kind": "issued_items", "format": "layout", "layout_id": str(layout.id)},
                       files={"file": ("f.txt", f"{account.account_number}|8001|10.00|2026-10-15|Zed\n".encode(), "text/plain")})
    assert resp.status_code == 202
    assert client.post("/api/v1/bulk-uploads", headers=headers, data={"kind": "issued_items", "format": "layout"},
                       files={"file": ("f.txt", b"x", "text/plain")}).status_code == 422

    monkeypatch.setattr(get_settings(), "auto_import_dropbox_dir", str(tmp_path))
    inbox = tmp_path / tenant.slug / "issued_items" / "inbox"
    inbox.mkdir(parents=True)
    dropped = inbox / "drop.txt"
    dropped.write_text(f"{account.account_number}|8002|11.00|2026-10-15|Yan\n")
    import os
    import time

    old = time.time() - 600
    os.utime(dropped, (old, old))
    dropbox_import_service.scan_and_import_tenant(db_session, tenant)
    checks = set(db_session.execute(select(IssuedItem.check_number).where(IssuedItem.tenant_id == tenant.id)).scalars())
    assert {"8001", "8002"} <= checks


# --- Fixing failed rows ------------------------------------------------------------------------------


def test_failed_rows_are_fixed_in_the_browser_and_backed_out_with_the_upload(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fix-rows")
    csrf = _login(client, tenant.slug, users["admin"].email)
    content = (f"account_number,check_number,amount,payee_name,issue_date\n"
               f"{account.account_number},6001,150.00,Acme,2026-01-15\n"
               f"{account.account_number},6002,1O.00,Bob,2026-01-15\n"
               f"999999,6003,20.00,Cy,2026-01-15\n").encode()
    resp, upload_id = _upload(client, csrf, content)
    assert "1 of 3 succeeded" in resp.text and f"/ui/bulk-uploads/{upload_id}/fix" in resp.text
    results = {r.row_label: r for r in db_session.execute(select(BulkUploadRowResult)).scalars() if not r.success}
    assert results["row 3"].error_field == "amount" and results["row 4"].error_field == "account_number"
    assert "<strong>Amount:</strong>" in resp.text

    page = client.get(f"/ui/bulk-uploads/{upload_id}/fix").text
    assert 'name="row_1__amount" value="1O.00"' in page and 'aria-invalid="true"' in page and "contains a letter" in page

    fixed = client.post(f"/ui/bulk-uploads/{upload_id}/fix", data={
        "csrf_token": csrf, "fix": ["1", "2"], "row_1__amount": "10.00", "row_2__account_number": "888888"}, follow_redirects=True)
    assert "1 row imported. 1 still needs fixing." in fixed.text
    assert 'name="row_2__account_number" value="888888"' in fixed.text and "No account found" in fixed.text
    assert db_session.execute(select(IssuedItem).where(IssuedItem.check_number == "6002")).scalar_one().amount == Decimal("10.00")
    corrections = list(db_session.execute(select(BulkUploadCorrection)).scalars())
    assert {(c.row_index, c.success) for c in corrections} == {(1, True), (2, False)}
    assert corrections[0].corrected_by_user_id == users["admin"].id
    audit = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.summary.like("%fixing a failed row%"))).scalars().all()
    assert len(audit) == 1

    detail = client.get(f"/ui/bulk-uploads/{upload_id}").text
    assert "Rows fixed in the browser" in detail and '<span class="badge badge-ok">fixed</span>' in detail
    client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf})
    db_session.expire_all()
    assert db_session.execute(select(IssuedItem).where(IssuedItem.check_number == "6002")).scalar_one().status == IssuedItemStatus.VOIDED


def test_a_file_in_other_columns_offers_a_layout_from_itself(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fix-offer")
    csrf = _login(client, tenant.slug, users["admin"].email)
    resp, upload_id = _upload(client, csrf, SAMPLE, "acme.csv")
    assert f"/ui/layouts/new?kind=issued_items&amp;from_upload={upload_id}" in resp.text
    builder = client.get(f"/ui/layouts/new?from_upload={upload_id}").text
    assert "acme.csv" in builder and 'name="assign_4"' in builder and "Pay To" in builder


# --- In a real browser ---------------------------------------------------------------------------


def test_the_builder_fix_page_and_drop_zone_in_a_browser(client, db_session, tenant_factory):
    import json
    from pathlib import Path

    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    tenant, account, users = tenant_factory.make(slug="layout-browser")
    csrf = _login(client, tenant.slug, users["admin"].email)
    _resp, other_columns = _upload(client, csrf, SAMPLE, "acme.csv")
    _resp, with_errors = _upload(client, csrf, (f"account_number,check_number,amount,payee_name,issue_date\n"
                                                f"{account.account_number},6101,1O.00,Bob,2026-01-15\n").encode())
    axe = (Path(__file__).resolve().parent.parent / "fixtures" / "axe" / "axe.min.js").read_text()

    def serve(route):
        request = route.request
        response = client.request(request.method, request.url.replace("http://testserver", ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=False)
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    def problems(page) -> list[str]:
        page.add_script_tag(content=axe)
        results = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
        return [f"{v['id']}: {n['target']}" for v in results["violations"] for n in v["nodes"]]

    try:
        context = browser.new_context(viewport={"width": 390, "height": 844}, bypass_csp=True)
        context.route("http://testserver/**", serve)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(error))

        for path in (f"/ui/layouts/new?from_upload={other_columns}", f"/ui/bulk-uploads/{with_errors}/fix", f"/ui/bulk-uploads/{with_errors}"):
            page.goto("http://testserver" + path)
            assert problems(page) == [], path
            wide = page.evaluate("""() => Array.from(document.querySelectorAll('body *')).filter(e => e.getBoundingClientRect().right > window.innerWidth + 1
                && !e.closest('.table-scroll')).slice(0, 6).map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right))
""")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{path} scrolls sideways on a phone: {wide}"

        # The fixed-width helper: select a field's characters in the sample, use them.
        page.goto("http://testserver/ui/layouts/new?template=fixed_width")
        line = _fixed_line("1001", "5001", "12345", "10152026", "Acme")
        page.set_input_files('input[name="sample"]', files=[{"name": "issue.txt", "mimeType": "text/plain", "buffer": (line + "\n").encode()}])
        assert "Chosen: issue.txt" in page.inner_text(".dropzone-hint")
        page.click('button[name="action"][value="preview"]')
        page.wait_for_selector("[data-sample-lines] .sample-line")
        # The builder's script shows its tools just before it listens for selections: select only after.
        page.wait_for_selector("[data-selection-tools]:not([hidden])")
        page.evaluate("""() => {
            const text = document.querySelector('[data-sample-lines] .sample-line').firstChild;
            const range = document.createRange();
            range.setStart(text, 12); range.setEnd(text, 22);
            const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        }""")
        page.wait_for_function("document.querySelector('[data-selection-status]').textContent.includes('start 13')")
        page.select_option("[data-selection-field]", "check_number")
        page.click("[data-use-selection]")
        assert page.input_value('[name="start_check_number"]') == "13" and page.input_value('[name="length_check_number"]') == "10"
        assert page.locator("text=Acme").count() >= 1  # the preview parsed the line

        # Dropping a file onto an upload page's zone chooses it.
        page.goto("http://testserver/ui/issued-items/bulk")
        page.evaluate("""() => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['a,b\\n'], 'dropped.csv', {type: 'text/csv'}));
            document.querySelector('.dropzone').dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
        }""")
        assert page.evaluate("document.querySelector('input[name=upload_file]').files[0].name") == "dropped.csv"
        assert "Chosen: dropped.csv" in page.inner_text(".dropzone-hint")
        assert not errors, json.dumps([str(e) for e in errors])
    finally:
        browser.close()
        runner.stop()
