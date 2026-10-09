# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OIDC back-channel logout and third-party initiated login (SDK_PLAN.md phase F;
web/routers/oidc_federation.py), against the fake provider's real signatures."""

from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc.jwk import RSAKey
from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.sso_connection import SsoProtocol, SsoProvider
from openclear.services import sso_service
from openclear.services.sso_service import SsoConnectionInput
from tests.test_auth.oidc_helpers import FakeOidcProvider, sign_logout_token


@pytest.fixture
def provider(monkeypatch):
    """The fake provider's discovery document and keys; the OAuth client itself stays real,
    so authorization URLs are built the way they are in production."""
    import openclear.auth.oidc_service as oidc_service

    fake = FakeOidcProvider()
    monkeypatch.setattr(oidc_service, "_get_discovery_document", lambda issuer: fake.discovery_document())
    monkeypatch.setattr(oidc_service, "_get_jwks", lambda jwks_uri: fake.jwks())
    return fake


def _connection(db_session, tenant, provider):
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OKTA, protocol=SsoProtocol.OIDC, display_name="Okta", issuer=provider.issuer,
        client_id=provider.client_id, client_secret="s", groups_claim_name="groups", auto_provision=False,
    ))
    db_session.commit()
    return connection


def _path(connection, what):
    return f"/ui/sso/oidc/{connection.tenant_id}/{connection.id}/{what}"


def test_signing_out_at_the_provider_ends_the_persons_sessions(client, db_session, tenant_factory, provider):
    tenant, _account, users = tenant_factory.make(slug="bcl-basic")
    connection = _connection(db_session, tenant, provider)
    person = users["approver"]
    sso_service.record_identity(db_session, tenant.id, connection, "00u-approver", person)
    db_session.commit()
    version = person.token_version

    done = client.post(_path(connection, "backchannel-logout"), data={"logout_token": sign_logout_token(provider, sub="00u-approver")})
    assert done.status_code == 200 and done.headers["cache-control"] == "no-store"
    db_session.refresh(person)
    assert person.token_version == version + 1
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "user.logout_sso")).scalar_one().actor_user_id == person.id

    replay = client.post(_path(connection, "backchannel-logout"), data={"logout_token": sign_logout_token(provider, sub="00u-approver")})
    assert replay.status_code == 400  # each logout token once
    unknown = client.post(_path(connection, "backchannel-logout"), data={"logout_token": sign_logout_token(provider, sub="00u-nobody", jti="l2")})
    assert unknown.status_code == 200  # nothing to do, and nothing to reveal


@pytest.mark.parametrize("case", ["no_event", "nonce", "wrong_audience", "stale", "no_sub", "wrong_key", "wrong_connection"])
def test_bad_logout_tokens_are_refused(case, client, db_session, tenant_factory, provider):
    tenant, _account, users = tenant_factory.make(slug=f"bcl-{case.replace('_', '-')}")
    connection = _connection(db_session, tenant, provider)
    sso_service.record_identity(db_session, tenant.id, connection, "00u-x", users["admin"])
    db_session.commit()
    version = users["admin"].token_version
    kwargs = {"sub": "00u-x", "jti": f"j-{case}"}
    if case == "no_event":
        kwargs["with_event"] = False
    elif case == "nonce":
        kwargs["nonce"] = "n"
    elif case == "wrong_audience":
        kwargs["aud"] = "someone-else"
    elif case == "stale":
        kwargs["iat_delta_seconds"] = -3600
    elif case == "no_sub":
        kwargs["sub"] = None
    elif case == "wrong_key":
        kwargs["key"] = RSAKey.generate_key(2048, parameters={"kid": "test-kid"}, private=True)
    path = _path(connection, "backchannel-logout")
    if case == "wrong_connection":
        path = path.replace(str(tenant.id), str(tenant_factory.make(slug="bcl-elsewhere")[0].id))
    response = client.post(path, data={"logout_token": sign_logout_token(provider, **kwargs)})
    assert response.status_code == 400, case
    db_session.refresh(users["admin"])
    assert users["admin"].token_version == version


def test_a_portal_tile_starts_a_normal_sign_in(client, db_session, tenant_factory, provider, monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "public_base_url", "https://openclear.example")
    tenant, _account, _users = tenant_factory.make(slug="tpi-basic")
    connection = _connection(db_session, tenant, provider)

    start = client.get(_path(connection, "initiate"), params={
        "iss": provider.issuer + "/", "target_link_uri": "https://openclear.example/ui/exceptions?status=open", "login_hint": "pat@bank.example",
    }, follow_redirects=False)
    assert start.status_code == 303
    location = urlsplit(start.headers["location"])
    query = parse_qs(location.query)
    assert location.path == f"/ui/login/sso/{connection.id}/start" and query["next"] == ["/ui/exceptions?status=open"]

    authorize = client.get(start.headers["location"], follow_redirects=False)
    assert authorize.status_code == 303
    sent = parse_qs(urlsplit(authorize.headers["location"]).query)
    assert sent["login_hint"] == ["pat@bank.example"] and sent["code_challenge_method"] == ["S256"] and "nonce" in sent

    elsewhere = client.get(_path(connection, "initiate"), params={"iss": provider.issuer, "target_link_uri": "https://evil.example/ui/"},
                           follow_redirects=False)
    assert parse_qs(urlsplit(elsewhere.headers["location"]).query)["next"] == ["/ui/"]
    assert client.get(_path(connection, "initiate"), params={"iss": "https://other-idp.example"}).status_code == 404
