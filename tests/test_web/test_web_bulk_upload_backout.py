# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_back_out_issued_items_upload_end_to_end(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-backout-issued-items")
    csrf = _login(client, tenant.slug, users["admin"].email)

    content = f"account_number,check_number,amount,payee_name,issue_date\n{account.account_number},5001,150.00,Vendor A,2026-01-01\n".encode()
    upload_resp = client.post(
        "/ui/issued-items/bulk", data={"csrf_token": csrf}, files={"upload_file": ("items.csv", content, "text/csv")}
    )
    assert "1 of 1 succeeded" in upload_resp.text

    items_page = client.get("/ui/issued-items")
    assert "5001" in items_page.text

    import re

    match = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text)
    assert match is not None
    upload_id = match.group(1)

    detail_page = client.get(f"/ui/bulk-uploads/{upload_id}")
    assert "Back out this upload" in detail_page.text

    backout_resp = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf})
    assert backout_resp.status_code == 200
    assert "1 of 1 succeeded" in backout_resp.text

    items_page_after = client.get("/ui/issued-items?status=voided")
    assert "5001" in items_page_after.text


def test_back_out_twice_is_refused(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-backout-twice")
    csrf = _login(client, tenant.slug, users["admin"].email)

    content = f"account_number,check_number,amount,payee_name,issue_date\n{account.account_number},5101,50.00,Vendor A,2026-01-01\n".encode()
    upload_resp = client.post(
        "/ui/issued-items/bulk", data={"csrf_token": csrf}, files={"upload_file": ("items.csv", content, "text/csv")}
    )

    import re

    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text).group(1)

    first = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf})
    assert first.status_code == 200

    second = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf}, follow_redirects=False)
    assert second.status_code == 303
    assert "error" in second.headers["location"]


def test_upload_with_nothing_tracked_cannot_be_backed_out(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-backout-nothing-tracked")
    csrf = _login(client, tenant.slug, users["admin"].email)

    # Every row fails to parse -> nothing succeeds -> nothing gets tracked
    content = b"account_number,check_number,amount,payee_name,issue_date\nunknown-account,5201,not-a-number,Vendor A,bad-date\n"
    upload_resp = client.post(
        "/ui/issued-items/bulk", data={"csrf_token": csrf}, files={"upload_file": ("items.csv", content, "text/csv")}
    )
    assert "0 of 1 succeeded" in upload_resp.text

    import re

    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text).group(1)

    detail_page = client.get(f"/ui/bulk-uploads/{upload_id}")
    assert "Back out this upload" not in detail_page.text

    resp = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_back_out_requires_permission(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="web-backout-permission")
    admin_csrf = _login(client, tenant.slug, users["admin"].email)

    content = f"account_number,check_number,amount,payee_name,issue_date\n{account.account_number},5301,20.00,Vendor A,2026-01-01\n".encode()
    upload_resp = client.post(
        "/ui/issued-items/bulk", data={"csrf_token": admin_csrf}, files={"upload_file": ("items.csv", content, "text/csv")}
    )
    import re

    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text).group(1)

    viewer_csrf = _login(client, tenant.slug, users["viewer"].email)
    resp = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": viewer_csrf}, follow_redirects=False)
    assert resp.status_code == 403


def test_back_out_users_upload_deactivates_membership(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-backout-users")
    csrf = _login(client, tenant.slug, users["admin"].email)

    content = "email,security_group,password\nbulk-new-user@example.com,Preparer,hunter2-hunter2\n".encode()
    upload_resp = client.post(
        "/ui/users/bulk", data={"csrf_token": csrf}, files={"upload_file": ("users.csv", content, "text/csv")}
    )
    assert "1 created" in upload_resp.text

    import re

    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text).group(1)

    backout_resp = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": csrf})
    assert backout_resp.status_code == 200
    assert "1 of 1 succeeded" in backout_resp.text

    users_page = client.get("/ui/users")
    assert "deactivated" in users_page.text


def test_back_out_check_images_upload_requires_dual_permission_and_reverses(client, db_session, tenant_factory):
    import io
    import zipfile

    from PIL import Image

    tenant, account, users = tenant_factory.make(slug="web-backout-checkimg")
    csrf = _login(client, tenant.slug, users["admin"].email)

    issued_resp = client.post(
        "/ui/issued-items",
        data={
            "csrf_token": csrf, "account_id": str(account.id), "check_number": "6001", "amount": "88.00",
            "payee_name": "Vendor", "issue_date": "2026-01-01",
        },
        follow_redirects=False,
    )
    assert issued_resp.status_code == 303

    img = Image.new("RGB", (20, 10), "white")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    manifest = (
        "account_number,check_number,amount,presented_date,front_image_filename\n"
        f"{account.account_number},6001,88.00,2026-01-05,check6001.png\n"
    ).encode()
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as archive:
        archive.writestr("manifest.csv", manifest)
        archive.writestr("check6001.png", img_buf.getvalue())

    upload_resp = client.post(
        "/ui/check-images/bulk",
        data={"csrf_token": csrf, "format": "zip"},
        files={"upload_file": ("checks.zip", zip_buf.getvalue(), "application/zip")},
    )
    assert "1 of 1 succeeded" in upload_resp.text

    import re

    upload_id = re.search(r"/ui/bulk-uploads/([0-9a-f-]+)", upload_resp.text).group(1)

    issued_page = client.get("/ui/issued-items")
    assert "paid" in issued_page.text.lower()

    # a group with only check_image:write (not paid_item:write) must be refused
    from openclear.services import security_group_service, user_service

    scoped_group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="CheckImageOnly", permissions=frozenset({"check_image:write"}))
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="imageonly@web-backout-checkimg.example.com", password=TenantFactory.PASSWORD,
        security_group_id=scoped_group.id,
    )
    db_session.commit()
    scoped_csrf = _login(client, tenant.slug, "imageonly@web-backout-checkimg.example.com")
    denied = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": scoped_csrf}, follow_redirects=False)
    assert denied.status_code == 403

    # the original admin (has both permissions) can back it out
    admin_csrf = _login(client, tenant.slug, users["admin"].email)
    backout_resp = client.post(f"/ui/bulk-uploads/{upload_id}/back-out", data={"csrf_token": admin_csrf})
    assert backout_resp.status_code == 200
    assert "1 of 1 succeeded" in backout_resp.text

    issued_page_after = client.get("/ui/issued-items")
    assert "outstanding" in issued_page_after.text.lower()
