# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The online banking sign-on profile (SDK_PLAN.md phase F): a bank-wide connection whose
people belong to business customers named by a claim; RFC 8693 token exchange of the
provider's ID token for a token acting for the person; and acting for someone by the
provider's own id for them."""

from datetime import date

import pytest
from sqlalchemy import select

from openclear.auth.oidc_service import OidcClaims
from openclear.domain.account import Account
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.sso_connection import SsoProtocol, SsoProvider
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import api_client_service, customer_service, security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from tests.test_auth.oidc_helpers import FakeOidcProvider

EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"


@pytest.fixture
def provider(monkeypatch):
    import openclear.auth.oidc_service as oidc_service

    fake = FakeOidcProvider(issuer="https://mifos.example.com", client_id="openclear-at-mifos")
    monkeypatch.setattr(oidc_service, "_get_discovery_document", lambda issuer: fake.discovery_document())
    monkeypatch.setattr(oidc_service, "_get_jwks", lambda jwks_uri: fake.jwks())
    return fake


def _bank(db_session, tenant_factory, slug, provider):
    tenant, account, users = tenant_factory.make(slug=slug)
    acme = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="CIF-1", name="Acme Corp"))
    beta = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="CIF-2", name="Beta LLC"))
    acme_account = Account(tenant_id=tenant.id, account_number="ACME-OPER", name="Operating", customer_id=acme.id)
    db_session.add(acme_account)
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OTHER, protocol=SsoProtocol.OIDC, display_name="Mifos online banking", issuer=provider.issuer,
        client_id=provider.client_id, client_secret="s", groups_claim_name="roles", auto_provision=True, customer_claim="customer_number",
    ))
    for external, group in (("obp-approvers", "Approver"), ("obp-viewers", "Viewer")):
        sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group=external,
                                      security_group_id=security_group_service.get_security_group_by_name(db_session, tenant.id, group).id)
    db_session.commit()
    return tenant, users, connection, acme, beta, acme_account


def _client(db_session, tenant, connection=None, *, customer_id=None, can_act=True):
    admin = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Mifos backend", security_group_id=admin.id,
                                              customer_id=customer_id, can_act_for_users=can_act,
                                              sso_connection_id=connection.id if connection else None)
    db_session.commit()
    return issued


def _id_token(provider, *, sub="mifos-user-7", email="jo@acme.example", roles=("obp-approvers",), customer="CIF-1", **extra):
    from joserfc import jwt as joserfc_jwt
    import time

    claims = {"iss": provider.issuer, "aud": provider.client_id, "sub": sub, "email": email, "roles": list(roles),
              "exp": int(time.time()) + 300, **({"customer_number": customer} if customer else {}), **extra}
    return joserfc_jwt.encode({"alg": "RS256", "kid": "test-kid"}, claims, provider._key)


def _exchange(client, issued, subject_token, **extra):
    return client.post("/api/v1/oauth/token", data={
        "grant_type": EXCHANGE, "client_id": issued.client.client_id, "client_secret": issued.secret,
        "subject_token": subject_token, "subject_token_type": ID_TOKEN, **extra,
    })


def test_the_customer_claim_scopes_a_sign_in_to_that_customer(db_session, tenant_factory, provider):
    tenant, _users, connection, acme, _beta, _a = _bank(db_session, tenant_factory, "obp-claim", provider)
    signed_in = sso_service.complete_sso_login(db_session, tenant.id, connection, OidcClaims(
        email="jo@acme.example", subject="s1", groups=["obp-approvers"], customer_number="CIF-1"))
    assert signed_in.outcome == "success" and signed_in.membership.customer_id == acme.id
    for number in (None, "CIF-404"):
        refused = sso_service.complete_sso_login(db_session, tenant.id, connection, OidcClaims(
            email="kim@acme.example", subject="s2", groups=["obp-approvers"], customer_number=number))
        assert refused.outcome == "not_authorized"
    with pytest.raises(ValueError, match="bank-wide"):
        sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
            provider=SsoProvider.OTHER, display_name="x", issuer=provider.issuer, client_id="c", client_secret="s",
            groups_claim_name="roles", auto_provision=False, customer_id=acme.id, customer_claim="customer_number"))


def test_token_exchange_acts_for_the_person_with_what_both_may_do(client, db_session, tenant_factory, provider):
    tenant, _users, connection, acme, _beta, acme_account = _bank(db_session, tenant_factory, "obp-exchange", provider)
    issued = _client(db_session, tenant, connection)

    exchanged = _exchange(client, issued, _id_token(provider))
    assert exchanged.status_code == 200, exchanged.text
    body = exchanged.json()
    assert body["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token" and exchanged.headers["cache-control"] == "no-store"
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    items = client.get("/api/v1/issued-items", headers=headers)
    assert items.status_code == 200  # Approvers may read, and the session is Acme's
    entries = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars().all()
    assert {"api_client.token_exchange", "user.login_sso"} & {e.action for e in entries} == {"api_client.token_exchange"}

    membership = db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id,
                                                                   TenantMembership.customer_id == acme.id)).scalar_one()
    viewer_token = _exchange(client, issued, _id_token(provider, sub="mifos-user-8", email="vi@acme.example", roles=("obp-viewers",))).json()
    write = client.post("/api/v1/issued-items", headers={"Authorization": f"Bearer {viewer_token['access_token']}"}, json={
        "account_id": str(acme_account.id), "check_number": "OBP-1", "amount": "10.00", "payee_name": "V", "issue_date": str(date(2026, 3, 1))})
    assert write.status_code == 403  # a viewer can't write, whatever the client may do

    membership.is_active = False
    db_session.commit()
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 401  # re-checked on every request


@pytest.mark.parametrize("case", ["wrong_type", "not_linked", "not_allowed", "bad_signature", "expired", "no_group", "other_customer", "wrong_audience"])
def test_exchanges_that_must_be_refused(case, client, db_session, tenant_factory, provider):
    tenant, _users, connection, acme, beta, _a = _bank(db_session, tenant_factory, f"obp-x-{case.replace('_', '-')}", provider)
    issued = _client(db_session, tenant, connection)
    token = _id_token(provider)
    extra = {}
    expected = "invalid_grant"
    if case == "wrong_type":
        extra, expected = {"subject_token_type": "urn:ietf:params:oauth:token-type:jwt"}, "invalid_request"
    elif case == "not_linked":
        issued, expected = _client(db_session, tenant, None), "unauthorized_client"
    elif case == "not_allowed":
        with pytest.raises(api_client_service.ApiClientError):
            _client(db_session, tenant, connection, can_act=False)
        return
    elif case == "bad_signature":
        token = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    elif case == "expired":
        token = _id_token(provider, exp=1)
    elif case == "no_group":
        token = _id_token(provider, roles=("someone-elses-group",))
    elif case == "other_customer":
        issued = _client(db_session, tenant, connection, customer_id=beta.id)
    elif case == "wrong_audience":
        token = _id_token(provider, aud="a-different-app")
    response = _exchange(client, issued, token, **extra)
    assert response.status_code == 400 and response.json()["error"] == expected, (case, response.text)


def test_acting_for_someone_by_the_providers_id(client, db_session, tenant_factory, provider):
    tenant, _users, connection, _acme, _beta, _a = _bank(db_session, tenant_factory, "obp-subject", provider)
    issued = _client(db_session, tenant, connection)
    _exchange(client, issued, _id_token(provider, sub="mifos-user-9", email="al@acme.example"))  # first sign-in records their id
    plain = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                     "client_secret": issued.secret}).json()["access_token"]
    headers = {"Authorization": f"Bearer {plain}"}

    assert client.get("/api/v1/issued-items", headers={**headers, "OpenClear-Act-As-Subject": "mifos-user-9"}).status_code == 200
    assert client.get("/api/v1/issued-items", headers={**headers, "OpenClear-Act-As-Subject": "nobody"}).status_code == 403
    both = client.get("/api/v1/issued-items", headers={**headers, "OpenClear-Act-As-Subject": "mifos-user-9", "OpenClear-Act-As": "al@acme.example"})
    assert both.status_code == 400
