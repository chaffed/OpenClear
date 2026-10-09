# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SCIM 2.0 provisioning (SDK_PLAN.md phase F; services/scim_service.py, api/scim.py), driven
the way Okta and Entra ID drive it: tokens, users and groups, access from group mappings,
deactivation ending sessions, and the PATCH shapes each sends."""

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.sso_connection import SsoProtocol, SsoProvider
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import customer_service, scim_service, security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from tests.conftest import TenantFactory

PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _connection(db_session, tenant, *, customer_id=None, mappings=(("openclear-approvers", "Approver"), ("openclear-admins", "Admin"))):
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OKTA, protocol=SsoProtocol.OIDC, display_name="Okta", issuer="https://acme.okta.com", client_id="cid",
        client_secret="secret", groups_claim_name="groups", auto_provision=False, customer_id=customer_id,
    ))
    for priority, (external, group_name) in enumerate(mappings):
        group = security_group_service.get_security_group_by_name(db_session, tenant.id, group_name)
        sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group=external, security_group_id=group.id,
                                      priority=priority)
    token = scim_service.issue_token(db_session, connection)
    db_session.commit()
    return connection, {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}


def _user_payload(email, **extra):
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": email, "active": True,
            "name": {"givenName": "Pat", "familyName": "Lee"}, "emails": [{"value": email, "primary": True, "type": "work"}], **extra}


def _membership(db_session, tenant, email):
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    db_session.expire_all()
    return user, db_session.execute(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id)).scalars().first()


def test_tokens(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="scim-token")
    other, _a, _u = tenant_factory.make(slug="scim-token-other")
    connection, headers = _connection(db_session, tenant)
    assert client.get("/scim/v2/Users", headers=headers).status_code == 200

    missing = client.get("/scim/v2/Users")
    assert missing.status_code == 401 and missing.json()["schemas"] == [scim_service.ERROR_SCHEMA]
    forged = headers["Authorization"].replace(tenant.id.hex, other.id.hex)
    assert client.get("/scim/v2/Users", headers={"Authorization": forged}).status_code == 401

    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    page = client.post(f"/ui/admin/sso/{connection.id}/scim-token", data={"csrf_token": csrf})
    assert page.status_code == 200 and "scim_" in page.text and page.headers["cache-control"] == "no-store"
    assert client.get("/scim/v2/Users", headers=headers).status_code == 401  # the old token stopped working
    client.post(f"/ui/admin/sso/{connection.id}/scim-token/revoke", data={"csrf_token": csrf})
    db_session.expire_all()
    assert sso_service.get_connection(db_session, tenant.id, connection.id).scim_token_hash is None


def test_okta_style_provisioning_gives_access_through_groups(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="scim-okta")
    _connection_row, headers = _connection(db_session, tenant)

    group = client.post("/scim/v2/Groups", headers=headers, json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                                                                     "displayName": "openclear-approvers", "members": []})
    assert group.status_code == 201, group.text
    created = client.post("/scim/v2/Users", headers=headers, json=_user_payload("pat@acme.example", externalId="00u1"))
    assert created.status_code == 201 and created.headers["content-type"].startswith("application/scim+json")
    user_id = created.json()["id"]
    _user, membership = _membership(db_session, tenant, "pat@acme.example")
    assert membership is None  # no group yet, no access

    added = client.patch(f"/scim/v2/Groups/{group.json()['id']}", headers=headers, json={
        "schemas": [PATCH], "Operations": [{"op": "add", "path": "members", "value": [{"value": user_id}]}]})
    assert added.status_code == 204
    _user, membership = _membership(db_session, tenant, "pat@acme.example")
    approver = security_group_service.get_security_group_by_name(db_session, tenant.id, "Approver")
    assert membership.is_active and membership.security_group_id == approver.id

    found = client.get("/scim/v2/Users", headers=headers, params={"filter": 'userName eq "PAT@acme.example"'}).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["groups"][0]["display"] == "openclear-approvers"
    assert client.get("/scim/v2/Users", headers=headers, params={"filter": 'title co "x"'}).json()["scimType"] == "invalidFilter"
    duplicate = client.post("/scim/v2/Users", headers=headers, json=_user_payload("pat@acme.example"))
    assert duplicate.status_code == 409 and duplicate.json()["scimType"] == "uniqueness"

    # Okta deactivates with a pathless replace.
    user, _m = _membership(db_session, tenant, "pat@acme.example")
    version = user.token_version
    client.patch(f"/scim/v2/Users/{user_id}", headers=headers, json={"schemas": [PATCH], "Operations": [{"op": "replace", "value": {"active": False}}]})
    user, membership = _membership(db_session, tenant, "pat@acme.example")
    assert not membership.is_active and user.token_version == version + 1
    assert client.get(f"/scim/v2/Users/{user_id}", headers=headers).json()["active"] is False
    actions = {e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()}
    assert {"scim.user.create", "scim.group.create", "scim.group.update", "scim.user.update"} <= actions


def test_entra_style_patches_and_group_changes(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="scim-entra")
    _c, headers = _connection(db_session, tenant)
    user_id = client.post("/scim/v2/Users", headers=headers, json=_user_payload("sam@acme.example", externalId="obj-1")).json()["id"]
    group_id = client.post("/scim/v2/Groups", headers=headers, json={"displayName": "openclear-approvers",
                                                                       "members": [{"value": user_id}]}).json()["id"]

    # Entra ID: capitalised ops, paths, and string booleans.
    client.patch(f"/scim/v2/Users/{user_id}", headers=headers, json={"schemas": [PATCH], "Operations": [
        {"op": "Replace", "path": "name.givenName", "value": "Samantha"},
        {"op": "Add", "path": "title", "value": "Treasury analyst"},  # not kept, not an error
        {"op": "Replace", "path": "active", "value": "False"},
    ]})
    resource = client.get(f"/scim/v2/Users/{user_id}", headers=headers).json()
    assert resource["name"]["givenName"] == "Samantha" and resource["active"] is False
    client.patch(f"/scim/v2/Users/{user_id}", headers=headers, json={"schemas": [PATCH], "Operations": [{"op": "Replace", "path": "active", "value": "True"}]})
    assert _membership(db_session, tenant, "sam@acme.example")[1].is_active

    # Renaming the group to another mapped name moves them to that security group.
    client.patch(f"/scim/v2/Groups/{group_id}", headers=headers, json={"schemas": [PATCH], "Operations": [
        {"op": "Replace", "path": "displayName", "value": "openclear-admins"}]})
    admin = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    assert _membership(db_session, tenant, "sam@acme.example")[1].security_group_id == admin.id

    # Removing them from the group (Entra's filtered path) takes their access away.
    client.patch(f"/scim/v2/Groups/{group_id}", headers=headers, json={"schemas": [PATCH], "Operations": [
        {"op": "Remove", "path": f'members[value eq "{user_id}"]'}]})
    assert not _membership(db_session, tenant, "sam@acme.example")[1].is_active

    assert client.delete(f"/scim/v2/Users/{user_id}", headers=headers).status_code == 204
    assert client.get(f"/scim/v2/Users/{user_id}", headers=headers).json()["active"] is False  # deactivated, not deleted
    assert client.delete(f"/scim/v2/Groups/{group_id}", headers=headers).status_code == 204
    assert client.get(f"/scim/v2/Groups/{group_id}", headers=headers).status_code == 404


def test_a_customer_connection_provisions_into_that_customer(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="scim-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Corp"))
    db_session.commit()
    _c, headers = _connection(db_session, tenant, customer_id=customer.id, mappings=(("acme-staff", "Preparer"),))
    user_id = client.post("/scim/v2/Users", headers=headers, json=_user_payload("cy@acme.example")).json()["id"]
    client.post("/scim/v2/Groups", headers=headers, json={"displayName": "acme-staff", "members": [{"value": user_id}]})
    membership = _membership(db_session, tenant, "cy@acme.example")[1]
    assert membership.is_active and membership.customer_id == customer.id


def test_an_existing_person_is_linked_by_email(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="scim-existing")
    _c, headers = _connection(db_session, tenant)
    person = users["preparer"]
    user_id = client.post("/scim/v2/Users", headers=headers, json=_user_payload(person.email)).json()["id"]
    client.post("/scim/v2/Groups", headers=headers, json={"displayName": "openclear-admins", "members": [{"value": user_id}]})
    admin = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user, membership = _membership(db_session, tenant, person.email)
    assert user.id == person.id and membership.security_group_id == admin.id  # the directory now governs their access

    changed = client.put(f"/scim/v2/Users/{user_id}", headers=headers, json=_user_payload("renamed@acme.example"))
    assert changed.status_code == 200 and changed.json()["emails"][0]["value"] == "renamed@acme.example"
