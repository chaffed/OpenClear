# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_users_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/users", follow_redirects=False)
    assert resp.status_code == 403


def test_add_brand_new_user(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-add-new")
    csrf = _login(client, tenant.slug, users["admin"].email)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    resp = client.post(
        "/ui/users",
        data={"csrf_token": csrf, "email": "newperson@example.com", "password": "hunter2-hunter2", "security_group_id": str(group.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/users")

    list_page = client.get("/ui/users")
    assert "newperson@example.com" in list_page.text
    assert "Preparer" in list_page.text


def test_add_existing_email_from_other_tenant_requires_confirmation(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-users-cross-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="web-users-cross-b")
    csrf = _login(client, tenant_b.slug, users_b["admin"].email)
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Viewer")

    resp = client.post(
        "/ui/users",
        data={"csrf_token": csrf, "email": users_a["preparer"].email, "password": "", "security_group_id": str(group_b.id)},
    )
    assert resp.status_code == 200
    assert "Confirm access" in resp.text

    # not yet granted — still only a member of tenant_a
    pending_memberships = user_service.list_memberships_for_user(db_session, users_a["preparer"].id)
    assert {m.tenant.id for m in pending_memberships} == {tenant_a.id}

    # now confirm it
    confirm_resp = client.post(
        "/ui/users/confirm",
        data={"csrf_token": csrf, "email": users_a["preparer"].email, "security_group_id": str(group_b.id)},
        follow_redirects=False,
    )
    assert confirm_resp.status_code == 303
    memberships = user_service.list_memberships_for_user(db_session, users_a["preparer"].id)
    assert {m.tenant.id for m in memberships} == {tenant_a.id, tenant_b.id}


def test_deactivate_and_reactivate_user(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-deactivate")
    csrf = _login(client, tenant.slug, users["admin"].email)
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]

    resp = client.post(f"/ui/users/{membership.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    list_page = client.get("/ui/users")
    assert "deactivated" in list_page.text

    resp = client.post(f"/ui/users/{membership.id}/reactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303


def test_unlock_button_shown_only_for_locked_users_and_clears_lockout(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-unlock")
    csrf = _login(client, tenant.slug, users["admin"].email)
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]

    list_page = client.get("/ui/users")
    assert f"/ui/users/{membership.id}/unlock" not in list_page.text

    user_service.unlock_user(db_session, tenant.id, membership.id)  # sanity: no-op on an unlocked user
    users["viewer"].failed_login_attempts = 5
    from datetime import datetime, timedelta, timezone

    users["viewer"].locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    db_session.commit()

    list_page = client.get("/ui/users")
    assert f"/ui/users/{membership.id}/unlock" in list_page.text

    resp = client.post(f"/ui/users/{membership.id}/unlock", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    db_session.expire_all()
    assert users["viewer"].locked_until is None
    assert users["viewer"].failed_login_attempts == 0

    list_page = client.get("/ui/users")
    assert f"/ui/users/{membership.id}/unlock" not in list_page.text


def test_bulk_upload_users_csv(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-bulk-csv")
    tenant_other, _account_other, users_other = tenant_factory.make(slug="web-users-bulk-csv-other")
    csrf = _login(client, tenant.slug, users["admin"].email)

    content = (
        "email,security_group,password\n"
        "bulknew@example.com,Preparer,hunter2-hunter2\n"
        f"{users_other['viewer'].email},Viewer,\n"
    ).encode()

    resp = client.post(
        "/ui/users/bulk",
        data={"csrf_token": csrf},
        files={"upload_file": ("users.csv", content, "text/csv")},
    )
    assert resp.status_code == 200
    assert "1 created" in resp.text
    assert "1 need" in resp.text

    confirm_value = f"{users_other['viewer'].email}::"
    # extract the security_group_id rendered into the hidden checkbox value — the value
    # is "email::security_group_id::customer_id", with customer_id blank here (bank-wide)
    import re

    match = re.search(rf'value="({re.escape(confirm_value)}[0-9a-f-]+::)"', resp.text)
    assert match is not None

    confirm_resp = client.post(
        "/ui/users/bulk/confirm", data={"csrf_token": csrf, "confirm": match.group(1)}, follow_redirects=False
    )
    assert confirm_resp.status_code == 200
    assert "1 created" in confirm_resp.text


def test_export_csv_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-export-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/users/export.csv", follow_redirects=False)
    assert resp.status_code == 403


def test_export_csv_contains_expected_rows(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-export-csv")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/users/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="users.csv"' in resp.headers["content-disposition"]

    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert {row["email"] for row in rows} == {u.email for u in users.values()}
    admin_row = next(row for row in rows if row["email"] == users["admin"].email)
    assert admin_row["security_group"] == "Admin"
    assert admin_row["customer"] == "bank-wide"
    assert admin_row["status"] == "active"


def test_export_json_contains_expected_rows(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-export-json")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/users/export.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert 'filename="users.json"' in resp.headers["content-disposition"]

    body = resp.json()
    assert {row["email"] for row in body} == {u.email for u in users.values()}
    admin_row = next(row for row in body if row["email"] == users["admin"].email)
    assert admin_row["security_group"] == "Admin"
    assert admin_row["customer"] == "bank-wide"
    assert admin_row["status"] == "active"


def test_export_csv_neutralizes_formula_injection_but_json_export_is_unaffected(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-export-csv-injection")
    _login(client, tenant.slug, users["admin"].email)

    group = security_group_service.create_security_group(
        db_session, tenant.id, security_group_service.SecurityGroupInput(name="=1+1", permissions=[])
    )
    user_service.add_user(db_session, tenant.id, email="injection-test@example.com", password="hunter2-hunter2", security_group_id=group.id)
    db_session.commit()

    import csv
    import io

    csv_resp = client.get("/ui/users/export.csv")
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    injected_row = next(row for row in rows if row["email"] == "injection-test@example.com")
    assert injected_row["security_group"] == "'=1+1"

    json_resp = client.get("/ui/users/export.json")
    body = json_resp.json()
    injected_json_row = next(row for row in body if row["email"] == "injection-test@example.com")
    assert injected_json_row["security_group"] == "=1+1"


def test_export_reflects_deactivated_status(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant, _account, users = tenant_factory.make(slug="web-users-export-deactivated")
    _login(client, tenant.slug, users["admin"].email)
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    user_service.deactivate_membership(db_session, tenant.id, membership.id)
    db_session.commit()

    body = client.get("/ui/users/export.json").json()
    viewer_row = next(row for row in body if row["email"] == users["viewer"].email)
    assert viewer_row["status"] == "deactivated"


def test_edit_entitlements_page_requires_permission(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant, _account, users = tenant_factory.make(slug="web-users-edit-forbidden")
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get(f"/ui/users/{membership.id}/edit", follow_redirects=False)
    assert resp.status_code == 403


def test_edit_entitlements_changes_security_group(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant, _account, users = tenant_factory.make(slug="web-users-edit-group")
    csrf = _login(client, tenant.slug, users["admin"].email)
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    approver_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")

    edit_form = client.get(f"/ui/users/{membership.id}/edit")
    assert edit_form.status_code == 200
    assert users["viewer"].email in edit_form.text

    resp = client.post(
        f"/ui/users/{membership.id}",
        data={"csrf_token": csrf, "security_group_id": str(approver_group.id), "customer_id": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_page = client.get("/ui/users")
    assert "Approver" in list_page.text

    db_session.expire_all()
    updated = TenantMembershipRepository(db_session, tenant.id).get(membership.id)
    assert updated.security_group_id == approver_group.id


def test_edit_entitlements_changes_customer_scope(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-users-edit-customer")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    db_session.commit()
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]

    resp = client.post(
        f"/ui/users/{membership.id}",
        data={"csrf_token": csrf, "security_group_id": str(membership.security_group_id), "customer_id": str(customer.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_page = client.get("/ui/users")
    assert "Acme Co" in list_page.text


def test_edit_entitlements_shows_error_on_scope_collision(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-users-edit-collision")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    scoped_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="scoped-collide@example.com", password="hunter2-hunter2",
        security_group_id=preparer_group.id, customer_id=customer.id,
    )
    db_session.commit()
    scoped_membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=scoped_user.id)[0]
    bankwide_membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["viewer"].id)[0]
    assert bankwide_membership.customer_id is None

    # try to move the viewer's own bank-wide membership onto the same customer scoped_user already holds --
    # no actual collision there since it's a different user; instead try moving scoped_user's membership
    # to bank-wide while they have no other membership -- to force a real collision, first grant them a
    # second, bank-wide membership too
    user_service.confirm_cross_tenant_membership(
        db_session, tenant.id, email="scoped-collide@example.com", security_group_id=preparer_group.id, customer_id=None
    )
    db_session.commit()

    resp = client.post(
        f"/ui/users/{scoped_membership.id}",
        data={"csrf_token": csrf, "security_group_id": str(preparer_group.id), "customer_id": ""},
    )
    assert resp.status_code == 422
    assert "already has a separate membership" in resp.text


def test_edit_entitlements_404s_for_unknown_membership(client, tenant_factory):
    import uuid

    tenant, _account, users = tenant_factory.make(slug="web-users-edit-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/users/{uuid.uuid4()}/edit")
    assert resp.status_code == 404


def test_edit_entitlements_404s_for_other_tenants_membership(client, db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-users-edit-cross-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="web-users-edit-cross-b")
    membership_a = TenantMembershipRepository(db_session, tenant_a.id).list(user_id=users_a["viewer"].id)[0]
    _login(client, tenant_b.slug, users_b["admin"].email)

    resp = client.get(f"/ui/users/{membership_a.id}/edit")
    assert resp.status_code == 404


def test_access_lookup_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-access-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/users/access", follow_redirects=False)
    assert resp.status_code == 403


def test_access_lookup_shows_existing_memberships(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-access-lookup")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/users/access?email={users['viewer'].email}")

    assert resp.status_code == 200
    assert "Viewer" in resp.text
    assert "bank-wide" in resp.text


def test_access_grant_direct_across_multiple_customers(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-access-grant-multi")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer_1 = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_2 = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Bookkeeper")

    resp = client.post(
        "/ui/users/access/grant",
        data={
            "csrf_token": csrf,
            "email": "bookkeeper@example.com",
            "password": "hunter2-hunter2",
            "security_group_id": str(group.id),
            "customer_ids": [str(customer_1.id), str(customer_2.id)],
        },
    )

    assert resp.status_code == 200
    assert "2 created" in resp.text
    memberships = user_service.get_access_for_email(db_session, tenant.id, "bookkeeper@example.com")
    assert {row.customer_name for row in memberships} == {customer_1.name, customer_2.name}
    assert all(row.security_group_name == "Bookkeeper" for row in memberships)


def test_access_grant_no_customers_selected_shows_error(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-access-grant-empty")
    csrf = _login(client, tenant.slug, users["admin"].email)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Bookkeeper")

    resp = client.post(
        "/ui/users/access/grant",
        data={"csrf_token": csrf, "email": "bookkeeper@example.com", "password": "hunter2-hunter2", "security_group_id": str(group.id)},
    )

    assert resp.status_code == 422
    assert "Select at least one customer" in resp.text


def test_access_grant_cross_tenant_email_needs_confirmation_then_confirms(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-users-access-cross-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="web-users-access-cross-b")
    csrf = _login(client, tenant_b.slug, users_b["admin"].email)
    customer_1 = customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_2 = customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    db_session.commit()
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Bookkeeper")

    resp = client.post(
        "/ui/users/access/grant",
        data={
            "csrf_token": csrf,
            "email": users_a["preparer"].email,
            "password": "",
            "security_group_id": str(group_b.id),
            "customer_ids": [str(customer_1.id), str(customer_2.id)],
        },
    )

    assert resp.status_code == 200
    assert "2 need" in resp.text
    assert user_service.get_access_for_email(db_session, tenant_b.id, users_a["preparer"].email) == []

    import re

    values = re.findall(r'name="confirm" value="([^"]+)"', resp.text)
    assert len(values) == 2

    confirm_resp = client.post(
        "/ui/users/bulk/confirm", data={"csrf_token": csrf, "confirm": values}, follow_redirects=False
    )
    assert confirm_resp.status_code == 200
    granted = user_service.get_access_for_email(db_session, tenant_b.id, users_a["preparer"].email)
    assert {row.customer_name for row in granted} == {customer_1.name, customer_2.name}
    assert all(row.security_group_name == "Bookkeeper" for row in granted)


def test_access_grant_require_webauthn_checkbox_direct_grant(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-users-access-grant-webauthn")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Bookkeeper")

    resp = client.post(
        "/ui/users/access/grant",
        data={
            "csrf_token": csrf,
            "email": "bookkeeper-key@example.com",
            "password": "hunter2-hunter2",
            "security_group_id": str(group.id),
            "customer_ids": [str(customer.id)],
            "require_webauthn": "true",
        },
    )

    assert resp.status_code == 200
    assert "1 created" in resp.text
    rows = user_service.get_access_for_email(db_session, tenant.id, "bookkeeper-key@example.com")
    assert len(rows) == 1
    assert rows[0].membership.require_webauthn is True


def test_access_grant_require_webauthn_checkbox_survives_needs_confirmation(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-users-access-webauthn-cross-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="web-users-access-webauthn-cross-b")
    csrf = _login(client, tenant_b.slug, users_b["admin"].email)
    customer = customer_service.create_customer(db_session, tenant_b.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    group_b = security_group_service.get_security_group_by_name(db_session, tenant_b.id, "Bookkeeper")

    resp = client.post(
        "/ui/users/access/grant",
        data={
            "csrf_token": csrf,
            "email": users_a["preparer"].email,
            "password": "",
            "security_group_id": str(group_b.id),
            "customer_ids": [str(customer.id)],
            "require_webauthn": "true",
        },
    )
    assert resp.status_code == 200
    assert "1 need" in resp.text

    import re

    values = re.findall(r'name="confirm" value="([^"]+)"', resp.text)
    assert len(values) == 1
    assert values[0].endswith("::1")

    confirm_resp = client.post("/ui/users/bulk/confirm", data={"csrf_token": csrf, "confirm": values})
    assert confirm_resp.status_code == 200
    granted = user_service.get_access_for_email(db_session, tenant_b.id, users_a["preparer"].email)
    assert len(granted) == 1
    assert granted[0].membership.require_webauthn is True


def _demote_to_viewer(db_session, tenant, user):
    from openclear.domain.tenant_membership import TenantMembership

    viewer = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name.lower() == "viewer")
    membership = db_session.query(TenantMembership).filter_by(user_id=user.id, tenant_id=tenant.id).one()
    user_service.update_membership(db_session, tenant.id, membership.id, security_group_id=viewer.id, customer_id=None)
    db_session.commit()


def test_security_group_reassignment_takes_effect_on_next_web_request(client, db_session, tenant_factory):
    """Demoting a logged-in user must not leave their old group's permissions active until
    their access token expires — permissions are resolved from the membership's CURRENT
    group, not the security_group_id the token was minted with."""
    tenant, _account, users = tenant_factory.make(slug="web-demote-immediate")
    _login(client, tenant.slug, users["admin"].email)
    assert client.get("/ui/users", follow_redirects=False).status_code == 200

    _demote_to_viewer(db_session, tenant, users["admin"])

    assert client.get("/ui/users", follow_redirects=False).status_code == 403


def test_security_group_reassignment_takes_effect_on_next_api_request(client, db_session, tenant_factory):
    from tests.conftest import login_headers

    tenant, _account, users = tenant_factory.make(slug="api-demote-immediate")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    assert client.get("/api/v1/users", headers=headers).status_code == 200

    _demote_to_viewer(db_session, tenant, users["admin"])

    assert client.get("/api/v1/users", headers=headers).status_code == 403
