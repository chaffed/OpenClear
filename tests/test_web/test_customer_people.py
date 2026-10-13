# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own administrators managing its people (CUSTOMER_ADMIN_PLAN.md CA1–CA2;
services/customer_people_service.py, web/routers/people.py): what they can do, and every
guardrail that keeps them inside their own customer."""

import re
import uuid

from sqlalchemy import select

from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory


def _setup(db_session, tenant_factory, slug="cp", *, self_manages=True):
    tenant, _house, users = tenant_factory.make(slug=slug)
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    other_customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    payroll = account_service.create_account(db_session, tenant.id, account_service.AccountInput(
        account_number="AC-PAY", name="Payroll", customer_id=customer.id))
    account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="AC-OPS", name="Ops", customer_id=customer.id))
    theirs = account_service.create_account(db_session, tenant.id, account_service.AccountInput(
        account_number="BT-1", name="Beta ops", customer_id=other_customer.id))
    admin_role = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Customer admin", permissions=["customer_user:manage", "issued_item:read", "issued_item:write"], offered_to_customers=True))
    clerk = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Clerk", permissions=["issued_item:read", "issued_item:write"], offered_to_customers=True))
    customer.self_manages_users = self_manages
    admin = user_service.create_user_with_membership(db_session, tenant.id, email=f"custadmin@{slug}.example.com", password=TenantFactory.PASSWORD,
                                                     security_group_id=admin_role.id, customer_id=customer.id)
    db_session.commit()
    return dict(tenant=tenant, users=users, customer=customer, other_customer=other_customer, payroll=payroll, theirs=theirs,
                admin_role=admin_role, clerk=clerk, admin=admin)


def _login(client, tenant, email, password=TenantFactory.PASSWORD):
    client.cookies.clear()
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": password, "csrf_token": csrf,
                                          "next": "/ui/"}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return client.cookies.get("csrf_token")


def _membership(db_session, tenant, user, customer):
    db_session.expire_all()
    return db_session.execute(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id, TenantMembership.customer_id == customer.id)).scalar_one()


def _invite(client, csrf, s, email, *, group=None, accounts=None):
    data = {"csrf_token": csrf, "email": email, "security_group_id": str((group or s["clerk"]).id)}
    if accounts:
        data.update(scope="some", account_id=[str(a.id) for a in accounts])
    return client.post("/ui/people/invite", data=data, follow_redirects=False)


def _link(db_session, email):
    db_session.expire_all()
    note = db_session.execute(select(Notification).where(
        Notification.notification_type == NotificationType.INVITATION, Notification.destination == email)
        .order_by(Notification.created_at.desc())).scalars().first()
    return re.search(r"(/ui/invitations/\S+)", note.body).group(1)


def test_people_need_the_banks_permission(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-off", self_manages=False)
    _login(client, s["tenant"], s["admin"].email)
    assert client.get("/ui/people").status_code == 403
    s["customer"].self_manages_users = True
    db_session.commit()
    assert client.get("/ui/people").status_code == 200
    assert "People" in client.get("/ui/settings").text
    s["customer"].self_manages_users = False  # the bank suspends it: at once
    db_session.commit()
    assert client.get("/ui/people").status_code == 403


def test_inviting_a_new_person_who_chooses_their_password(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-new")
    csrf = _login(client, s["tenant"], s["admin"].email)
    assert _invite(client, csrf, s, "pat@acme.example.com", accounts=[s["payroll"]]).status_code == 303
    assert db_session.execute(select(User).where(User.email == "pat@acme.example.com")).scalar_one_or_none() is None  # nothing yet
    assert "pat@acme.example.com" in client.get("/ui/people").text  # listed as invited

    link = _link(db_session, "pat@acme.example.com")
    client.cookies.clear()
    page = client.get(link)
    assert page.status_code == 200 and "Choose a password" in page.text and page.headers["cache-control"] == "no-store"
    csrf = client.cookies.get("csrf_token")
    assert client.post(link, data={"csrf_token": csrf, "password": "short", "password_confirm": "short"}).status_code == 422
    done = client.post(link, data={"csrf_token": csrf, "password": "A-much-better-passw0rd!", "password_confirm": "A-much-better-passw0rd!"},
                       follow_redirects=False)
    assert done.status_code == 303
    pat = db_session.execute(select(User).where(User.email == "pat@acme.example.com")).scalar_one()
    membership = _membership(db_session, s["tenant"], pat, s["customer"])
    assert membership.security_group_id == s["clerk"].id and membership.all_accounts is False
    assert "can't be used" in client.get(link).text  # once only
    _login(client, s["tenant"], "pat@acme.example.com", "A-much-better-passw0rd!")


def test_someone_who_exists_elsewhere_confirms_with_their_own_password(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-existing")
    elsewhere, _account, other_users = tenant_factory.make(slug="cp-otherbank")
    outsider = other_users["viewer"]
    csrf = _login(client, s["tenant"], s["admin"].email)
    assert _invite(client, csrf, s, outsider.email).status_code == 303
    link = _link(db_session, outsider.email)
    client.cookies.clear()
    assert "Confirm it's you" in client.get(link).text
    csrf = client.cookies.get("csrf_token")
    assert client.post(link, data={"csrf_token": csrf, "current_password": "wrong"}).status_code == 422
    db_session.expire_all()
    assert db_session.get(User, outsider.id).failed_login_attempts == 1  # counts toward their lockout
    assert client.post(link, data={"csrf_token": csrf, "current_password": TenantFactory.PASSWORD}, follow_redirects=False).status_code == 303
    assert _membership(db_session, s["tenant"], outsider, s["customer"]).is_active


def test_only_offered_roles_and_the_customers_own_accounts(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-guard")
    bank_admin_group = security_group_service.get_security_group_by_name(db_session, s["tenant"].id, "Admin")
    csrf = _login(client, s["tenant"], s["admin"].email)
    assert _invite(client, csrf, s, "x@acme.example.com", group=bank_admin_group).status_code == 422
    assert _invite(client, csrf, s, "x@acme.example.com", accounts=[s["theirs"]]).status_code == 422
    assert _invite(client, csrf, s, s["users"]["admin"].email, group=s["clerk"]).status_code == 303  # an invitation, never a silent link

    # People of another customer, and the bank's own staff, aren't reachable.
    beta = user_service.create_user_with_membership(db_session, s["tenant"].id, email="b@beta.example.com", password=TenantFactory.PASSWORD,
                                                    security_group_id=s["clerk"].id, customer_id=s["other_customer"].id)
    db_session.commit()
    beta_membership = _membership(db_session, s["tenant"], beta, s["other_customer"])
    assert client.get(f"/ui/people/{beta_membership.id}").status_code == 404
    assert client.post(f"/ui/people/{beta_membership.id}/deactivate", data={"csrf_token": csrf}).status_code == 404
    assert "b@beta.example.com" not in client.get("/ui/people").text
    # Nor the bank's own pages.
    assert client.get("/ui/users").status_code == 403


def test_changing_a_person_and_keeping_an_administrator(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-change")
    clerk = user_service.create_user_with_membership(db_session, s["tenant"].id, email="c@acme.example.com", password=TenantFactory.PASSWORD,
                                                     security_group_id=s["clerk"].id, customer_id=s["customer"].id)
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    clerk_m = _membership(db_session, s["tenant"], clerk, s["customer"])
    saved = client.post(f"/ui/people/{clerk_m.id}", data={"csrf_token": csrf, "security_group_id": str(s["clerk"].id), "scope": "some",
                                                          "account_id": str(s["payroll"].id), "require_webauthn": "true"}, follow_redirects=False)
    assert saved.status_code == 303
    clerk_m = _membership(db_session, s["tenant"], clerk, s["customer"])
    assert clerk_m.require_webauthn and not clerk_m.all_accounts

    admin_m = _membership(db_session, s["tenant"], s["admin"], s["customer"])
    refused = client.post(f"/ui/people/{admin_m.id}/deactivate", data={"csrf_token": csrf})
    assert refused.status_code == 422 and "last administrator" in refused.text
    demoted = client.post(f"/ui/people/{admin_m.id}", data={"csrf_token": csrf, "security_group_id": str(s["clerk"].id), "scope": "all"})
    assert demoted.status_code == 422
    # With a second administrator, either can step down.
    client.post(f"/ui/people/{clerk_m.id}", data={"csrf_token": csrf, "security_group_id": str(s["admin_role"].id), "scope": "all"})
    assert client.post(f"/ui/people/{admin_m.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303


def test_whole_identity_actions_only_for_people_who_belong_here_alone(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-identity")
    alone = user_service.create_user_with_membership(db_session, s["tenant"].id, email="alone@acme.example.com",
                                                     password=TenantFactory.PASSWORD, security_group_id=s["clerk"].id, customer_id=s["customer"].id)
    shared = s["users"]["preparer"]  # the bank's own staff, also given a place at this customer
    user_service.add_user(db_session, s["tenant"].id, email=shared.email, password="", security_group_id=s["clerk"].id,
                          customer_id=s["customer"].id)
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    alone_m = _membership(db_session, s["tenant"], alone, s["customer"])
    shared_m = _membership(db_session, s["tenant"], shared, s["customer"])

    reset = client.post(f"/ui/people/{alone_m.id}/reset-password", data={"csrf_token": csrf})
    assert reset.status_code == 200 and "Temporary password issued" in reset.text and reset.headers["cache-control"] == "no-store"
    for action in ("reset-password", "unlock", "sign-out"):
        refused = client.post(f"/ui/people/{shared_m.id}/{action}", data={"csrf_token": csrf})
        assert refused.status_code == 422 and "another organization" in refused.text, action
    assert "only they or your bank" in client.get(f"/ui/people/{shared_m.id}").text
    before = db_session.get(User, shared.id).token_version
    db_session.expire_all()
    assert db_session.get(User, shared.id).token_version == before


def test_withdrawn_and_expired_invitations(client, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    from openclear.domain.customer_admin import UserInvitation

    s = _setup(db_session, tenant_factory, slug="cp-expire")
    csrf = _login(client, s["tenant"], s["admin"].email)
    _invite(client, csrf, s, "late@acme.example.com")
    _invite(client, csrf, s, "gone@acme.example.com")
    late, gone = _link(db_session, "late@acme.example.com"), _link(db_session, "gone@acme.example.com")
    invitation = db_session.execute(select(UserInvitation).where(UserInvitation.email == "gone@acme.example.com")).scalar_one()
    assert client.post(f"/ui/people/invitations/{invitation.id}/revoke", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    db_session.execute(UserInvitation.__table__.update().where(UserInvitation.email == "late@acme.example.com")
                       .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    db_session.commit()
    client.cookies.clear()
    for link in (late, gone, "/ui/invitations/nonsense", f"/ui/invitations/{s['tenant'].id.hex}.forged"):
        assert "can't be used" in client.get(link).text, link


def test_many_invitations_from_a_file_and_the_activity_log(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-bulk")
    csrf = _login(client, s["tenant"], s["admin"].email)
    data = "email,role,accounts\none@acme.example.com,Clerk,\ntwo@acme.example.com,clerk,AC-PAY\nthree@acme.example.com,Admin,\nfour@acme.example.com,Clerk,BT-1\n"
    page = client.post("/ui/people/invite/bulk", data={"csrf_token": csrf}, files={"file": ("p.csv", data.encode(), "text/csv")})
    assert page.status_code == 200 and "2 invitations sent" in page.text
    assert "one your bank offers" in page.text and "Not your organization" in page.text
    exported = client.get("/ui/people/export.csv")
    assert exported.status_code == 200 and s["admin"].email in exported.text
    activity = client.get("/ui/people/activity").text
    assert "Invited 2 people from a file" in activity and "Exported the list" in activity


def test_the_bank_turns_it_on_and_offers_roles(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cp-bank", self_manages=False)
    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    page = client.get(f"/ui/customers/{s['customer'].id}").text
    assert "Self-service" in page
    saved = client.post(f"/ui/customers/{s['customer'].id}/self-service", data={"csrf_token": csrf, "manages_users": "true"},
                        follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()
    customer = customer_service.get_customer(db_session, s["tenant"].id, s["customer"].id)
    assert customer.self_manages_users and not customer.self_manages_sso
    assert s["admin"].email in client.get(f"/ui/customers/{s['customer'].id}").text  # its administrators

    viewer = security_group_service.get_security_group_by_name(db_session, s["tenant"].id, "Viewer")
    client.post(f"/ui/security-groups/{viewer.id}", data={"csrf_token": csrf, "name": "Viewer", "permissions": viewer.permissions,
                                                          "offered_to_customers": "true"})
    db_session.expire_all()
    assert security_group_service.get_security_group(db_session, s["tenant"].id, viewer.id).offered_to_customers
    assert "offered to customers" in client.get("/ui/security-groups").text


def test_a_customer_sign_on_session_cannot_switch_organizations(client, db_session, tenant_factory):
    """Guardrail 1: a customer's identity provider vouches for that customer only."""
    from openclear.auth.security import create_session_tokens, decode_token
    from openclear.services import session_service

    s = _setup(db_session, tenant_factory, slug="cp-confine")
    user_service.add_user(db_session, s["tenant"].id, email=s["admin"].email, password="", security_group_id=s["clerk"].id,
                          customer_id=s["other_customer"].id)
    db_session.commit()
    elsewhere = _membership(db_session, s["tenant"], s["admin"], s["other_customer"])
    admin_m = _membership(db_session, s["tenant"], s["admin"], s["customer"])

    tokens = create_session_tokens(user_id=s["admin"].id, tenant_id=s["tenant"].id, security_group_id=admin_m.security_group_id,
                                   customer_id=s["customer"].id, token_version=0, confined=True)
    refreshed, _tenant = session_service.refresh_session(db_session, tokens.refresh_token)
    assert decode_token(refreshed.access_token).get("cfd") == 1  # stays confined

    client.cookies.clear()
    client.get("/ui/login")
    client.cookies.set("access_token", tokens.access_token)
    csrf = client.cookies.get("csrf_token")
    page = client.get("/ui/switch-tenant")
    assert "open your other organizations" in page.text and "Beta" not in page.text
    refused = client.post("/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(elsewhere.id)}, follow_redirects=False)
    assert refused.status_code == 303 and "error=" in refused.headers["location"]

    # Signed in with a password instead, switching works as before.
    csrf = _login(client, s["tenant"], s["admin"].email)
    assert client.post("/ui/switch-tenant", data={"csrf_token": csrf, "membership_id": str(elsewhere.id)},
                       follow_redirects=False).headers["location"] == "/ui/"


def test_a_customer_sign_on_sets_confinement(db_session, tenant_factory):
    from openclear.auth.oidc_service import OidcClaims
    from openclear.auth.security import decode_token
    from openclear.services import sso_service
    from openclear.web.routers.auth import finish_sso_login

    s = _setup(db_session, tenant_factory, slug="cp-sso")
    connection = sso_service.create_connection(db_session, s["tenant"].id, sso_service.SsoConnectionInput(
        provider="okta", display_name="Acme IdP", issuer="https://idp.acme.example.com", client_id="x", client_secret="y",
        groups_claim_name="groups", auto_provision=False, customer_id=s["customer"].id))
    sso_service.add_group_mapping(db_session, s["tenant"].id, connection.id, external_group="staff", security_group_id=s["clerk"].id)
    db_session.commit()

    class _Request:
        cookies: dict = {}

    response = finish_sso_login(_Request(), db_session, s["tenant"].id, connection,
                                OidcClaims(subject="sub-1", email=s["admin"].email, groups=["staff"]), "/ui/", lambda m: m)
    cookie = next(v for k, v in response.raw_headers if k == b"set-cookie" and v.startswith(b"access_token="))
    token = cookie.split(b";")[0].split(b"=", 1)[1].decode()
    assert decode_token(token)["cfd"] == 1
