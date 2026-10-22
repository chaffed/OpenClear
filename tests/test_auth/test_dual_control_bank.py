# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's sensitive settings and people's access through dual control (DUAL_CONTROL_PLAN.md
DC1, DC2): each loosening waits for a second admin when the bank's switch is on; tightening never
waits."""

from sqlalchemy import select

from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.domain.api_client import ApiClient
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import change_request_service as changes
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.test_dual_control import _second_admin
from tests.test_web.test_web_bulk_ach import _login


def _bank(db_session, tenant_factory, slug, *, sensitive=False, access=False):
    tenant, _a, users = tenant_factory.make(slug=slug)
    second = _second_admin(db_session, tenant)
    tenant.sensitive_changes_need_approval, tenant.access_changes_need_approval = sensitive, access
    db_session.commit()
    return tenant, users, second


def _approve_all(db_session, tenant, approver):
    for change in changes.pending(db_session, tenant.id):
        changes.decide(db_session, tenant.id, change.id, approve=True, user_id=approver.id)
    db_session.commit()


def test_security_groups_wait_when_given_more(client, db_session, tenant_factory):
    tenant, users, second = _bank(db_session, tenant_factory, "dcb-groups", sensitive=True)
    csrf = _login(client, tenant.slug, users["admin"].email)
    empty = client.post("/ui/security-groups", data={"csrf_token": csrf, "name": "Auditors"}, follow_redirects=False)
    assert "created" in empty.headers["location"]  # no permissions: nothing to approve
    auditors = security_group_service.get_security_group_by_name(db_session, tenant.id, "Auditors")
    wider = client.post(f"/ui/security-groups/{auditors.id}", data={"csrf_token": csrf, "name": "Auditors",
                                                                     "permissions": ["audit_log:read"]}, follow_redirects=False)
    assert "Sent" in wider.headers["location"]
    db_session.expire_all()
    assert security_group_service.get_security_group(db_session, tenant.id, auditors.id).permissions == []
    _approve_all(db_session, tenant, second)
    assert security_group_service.get_security_group(db_session, tenant.id, auditors.id).permissions == ["audit_log:read"]
    narrower = client.post(f"/ui/security-groups/{auditors.id}", data={"csrf_token": csrf, "name": "Auditors"}, follow_redirects=False)
    assert "updated" in narrower.headers["location"]  # fewer permissions never wait


def test_a_new_api_client_works_only_once_approved(client, db_session, tenant_factory):
    tenant, users, second = _bank(db_session, tenant_factory, "dcb-clients", sensitive=True)
    viewer = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.post("/ui/admin/api-clients", data={"csrf_token": csrf, "name": "Core", "security_group_id": str(viewer.id)})
    assert "can't be used yet" in page.text
    client_row = db_session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant.id)).scalar_one()
    secret = page.text.split("<th>Client secret</th><td><code>")[1].split("<")[0]
    token = {"grant_type": "client_credentials", "client_id": client_row.client_id, "client_secret": secret}
    assert client.post("/api/v1/oauth/token", data=token).status_code == 401
    _approve_all(db_session, tenant, second)
    assert client.post("/api/v1/oauth/token", data=token).status_code == 200


def test_webhooks_embedding_and_self_service_wait(client, db_session, tenant_factory):
    from tests.items import _make_customer_with_account

    tenant, users, second = _bank(db_session, tenant_factory, "dcb-misc", sensitive=True)
    customer, _account = _make_customer_with_account(db_session, tenant, "DCB-1")
    db_session.commit()
    csrf = _login(client, tenant.slug, users["admin"].email)
    hook = client.post("/ui/admin/webhooks", data={"csrf_token": csrf, "url": "https://hooks.example.com/in", "event_types": ["exception.created"]})
    assert "It sends nothing yet" in hook.text
    client.post("/ui/settings/embedding", data={"csrf_token": csrf, "embed_allowed_origins": "https://bank.example.com"})
    client.post(f"/ui/customers/{customer.id}/self-service", data={"csrf_token": csrf, "manages_users": "true"})
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).embed_allowed_origins is None and not db_session.get(type(customer), customer.id).self_manages_users
    assert {c.kind for c in changes.pending(db_session, tenant.id)} == {"webhook.save", "embedding", "customer.self_service"}
    _approve_all(db_session, tenant, second)
    assert db_session.get(Tenant, tenant.id).embed_allowed_origins == "https://bank.example.com"
    assert db_session.get(type(customer), customer.id).self_manages_users


def test_sign_on_waits_and_a_scim_token_works_only_once_approved(client, db_session, tenant_factory, monkeypatch):
    from openclear.domain.sso_connection import SsoConnection

    tenant, users, second = _bank(db_session, tenant_factory, "dcb-sso", sensitive=True)
    csrf = _login(client, tenant.slug, users["admin"].email)
    created = client.post("/ui/admin/sso", data={"csrf_token": csrf, "provider": "okta", "protocol": "oidc", "display_name": "Okta",
                                                  "issuer": "https://idp.example.com", "client_id": "abc", "client_secret": "s3cret"},
                          follow_redirects=False)
    assert "off+for+now" in created.headers["location"].replace("%20", "+")
    connection = db_session.execute(select(SsoConnection).where(SsoConnection.tenant_id == tenant.id)).scalar_one()
    assert connection.is_active is False
    page = client.post(f"/ui/admin/sso/{connection.id}/scim-token", data={"csrf_token": csrf})
    token = page.text.split('<code class="secret">')[1].split("<")[0]
    assert "This token works only once" in page.text
    scim = {"Authorization": f"Bearer {token}"}
    assert client.get("/scim/v2/Users", headers=scim).status_code == 401
    _approve_all(db_session, tenant, second)
    db_session.expire_all()
    assert db_session.get(SsoConnection, connection.id).is_active
    assert client.get("/scim/v2/Users", headers=scim).status_code == 200


def test_access_changes_wait_and_removing_never_does(client, db_session, tenant_factory):
    tenant, users, second = _bank(db_session, tenant_factory, "dcb-access", access=True)
    viewer = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    admin = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    csrf = _login(client, tenant.slug, users["admin"].email)
    added = client.post("/ui/users", data={"csrf_token": csrf, "email": "newbie@dcb-access.example.com", "password": "Correct-Horse-9-Battery",
                                           "security_group_id": str(viewer.id)}, follow_redirects=False)
    assert "second+person" in added.headers["location"].replace("%20", "+")
    result = authenticate_password(db_session, tenant.slug, "newbie@dcb-access.example.com", "Correct-Horse-9-Battery")
    assert result.outcome == PasswordLoginOutcome.PENDING_APPROVAL
    _approve_all(db_session, tenant, second)
    assert authenticate_password(db_session, tenant.slug, "newbie@dcb-access.example.com", "Correct-Horse-9-Battery").outcome == PasswordLoginOutcome.SUCCESS

    membership = db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id,
                                                                   TenantMembership.user_id == users["viewer"].id)).scalar_one()
    up = client.post(f"/ui/users/{membership.id}", data={"csrf_token": csrf, "security_group_id": str(admin.id)}, follow_redirects=False)
    assert "second+person" in up.headers["location"].replace("%20", "+")
    db_session.expire_all()
    assert db_session.get(TenantMembership, membership.id).security_group_id == viewer.id
    _approve_all(db_session, tenant, second)
    assert db_session.get(TenantMembership, membership.id).security_group_id == admin.id
    down = client.post(f"/ui/users/{membership.id}", data={"csrf_token": csrf, "security_group_id": str(viewer.id)}, follow_redirects=False)
    assert "Entitlements+updated" in down.headers["location"]  # less never waits
    off = client.post(f"/ui/users/{membership.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert off.status_code == 303
    back = client.post(f"/ui/users/{membership.id}/reactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert "second+person" in back.headers["location"].replace("%20", "+")
    db_session.expire_all()
    assert db_session.get(TenantMembership, membership.id).is_active is False
