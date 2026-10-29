# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer connects its own identity provider, a real Keycloak, and its person signs in
through Keycloak's own login form (CUSTOMER_ADMIN_PLAN.md, "Testing"). Run by
scripts/sso_live_check.py, which starts and configures Keycloak; skipped otherwise."""

import html
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

from openclear.auth.security import decode_token
from openclear.config import get_settings
from openclear.domain.sso_connection import SsoProvider
from openclear.services import customer_sign_on_service
from openclear.services.sso_service import SsoConnectionInput
from tests.test_web.test_customer_sign_on import _sign_on_setup

ISSUER = os.environ.get("OPENCLEAR_TEST_KEYCLOAK_ISSUER")
pytestmark = pytest.mark.skipif(not ISSUER, reason="run by scripts/sso_live_check.py, against a real Keycloak")


def _keycloak_login(authorization_url: str, email: str, password: str) -> str:
    """Fills in Keycloak's login form like a browser; returns where it sends the browser back to."""
    browser = httpx.Client(follow_redirects=False, timeout=30)
    page = browser.get(authorization_url)
    assert page.status_code == 200, page.text[:300]
    action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', page.text).group(1))
    # Keycloak marks its login cookies Secure; browsers treat localhost as secure, httpx doesn't.
    cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)
    answer = browser.post(urljoin(authorization_url, action), data={"username": email, "password": password, "credentialId": ""},
                          headers={"Cookie": cookies})
    if answer.status_code not in (302, 303):
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style|head).*?</\1>", "", answer.text))
        raise AssertionError(f"Keycloak answered {answer.status_code}: {' '.join(text.split())[:400]}")
    return answer.headers["location"]


def test_a_customer_signs_in_through_its_own_keycloak(client, db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "oidc_allow_private_hosts", True)  # Keycloak is on this machine
    s = _sign_on_setup(db_session, tenant_factory, "kc-live")
    connection = customer_sign_on_service.create(db_session, s["tenant"].id, s["customer"].id, SsoConnectionInput(
        provider=SsoProvider.KEYCLOAK, display_name="Acme Keycloak",
        issuer=ISSUER, client_id=os.environ["OPENCLEAR_TEST_KEYCLOAK_CLIENT_ID"],
        client_secret=os.environ["OPENCLEAR_TEST_KEYCLOAK_CLIENT_SECRET"], groups_claim_name="groups", auto_provision=True))
    customer_sign_on_service.add_mapping(db_session, s["tenant"].id, s["customer"].id, connection.id, external_group="acme-staff",
                                         security_group_id=s["clerk"].id)
    domain = customer_sign_on_service.add_domain(db_session, s["tenant"].id, s["customer"].id, "acme.example.com")
    domain.verified_at = datetime.now(timezone.utc)  # DNS isn't what this checks
    db_session.commit()

    start = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(s["tenant"].id), "next": "/ui/"},
                       follow_redirects=False)
    assert start.status_code == 303 and start.headers["location"].startswith(ISSUER), start.text[:300]
    back = _keycloak_login(start.headers["location"], os.environ["OPENCLEAR_TEST_KEYCLOAK_EMAIL"], os.environ["OPENCLEAR_TEST_KEYCLOAK_PASSWORD"])
    callback = urlsplit(back)
    signed_in = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert signed_in.status_code == 303 and signed_in.headers["location"] == "/ui/", signed_in.text[:500]

    claims = decode_token(client.cookies.get("access_token"))
    assert claims["customer_id"] == str(s["customer"].id) and claims["cfd"] == 1  # confined to the customer
    from sqlalchemy import select

    from openclear.domain.sso_connection import SsoIdentity
    from openclear.domain.user import User

    person = db_session.execute(select(User).where(User.email == os.environ["OPENCLEAR_TEST_KEYCLOAK_EMAIL"])).scalar_one()
    identity = db_session.execute(select(SsoIdentity).where(SsoIdentity.connection_id == connection.id)).scalar_one()
    assert identity.user_id == person.id  # linked by Keycloak's own id for them, for later sign-ins
    assert client.get("/ui/").status_code == 200


def _keycloak_admin() -> httpx.Client:
    base = ISSUER.split("/realms/")[0]
    token = httpx.post(f"{base}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin",
        "password": os.environ["OPENCLEAR_TEST_KEYCLOAK_ADMIN_PASSWORD"]}).json()["access_token"]
    return httpx.Client(base_url=f"{base}/admin/realms/{ISSUER.rsplit('/', 1)[-1]}", headers={"Authorization": f"Bearer {token}"}, timeout=30)


def test_a_customer_signs_in_through_its_own_keycloak_by_saml(client, db_session, tenant_factory, monkeypatch):
    from openclear.auth import saml_service
    from openclear.domain.sso_connection import SsoProtocol

    monkeypatch.setattr(get_settings(), "oidc_allow_private_hosts", True)  # Keycloak is on this machine, over http
    # SAML needs real-looking URLs of ours (python3-saml refuses "testserver"); Keycloak never calls them.
    monkeypatch.setattr(get_settings(), "public_base_url", "https://openclear.bank.example")
    s = _sign_on_setup(db_session, tenant_factory, "kc-saml")
    descriptor = httpx.get(f"{ISSUER}/protocol/saml/descriptor").text
    certificate = re.search(r"<(?:ds:|dsig:)?X509Certificate>([^<]+)<", descriptor).group(1).strip()
    connection = customer_sign_on_service.create(db_session, s["tenant"].id, s["customer"].id, SsoConnectionInput(
        provider=SsoProvider.KEYCLOAK, protocol=SsoProtocol.SAML, display_name="Acme Keycloak SAML", issuer=ISSUER,
        client_id=None, client_secret=None, groups_claim_name="groups", auto_provision=True,
        saml_sso_url=f"{ISSUER}/protocol/saml", saml_idp_certificate=f"-----BEGIN CERTIFICATE-----\n{certificate}\n-----END CERTIFICATE-----"))
    customer_sign_on_service.add_mapping(db_session, s["tenant"].id, s["customer"].id, connection.id, external_group="acme-staff",
                                         security_group_id=s["clerk"].id)
    domain = customer_sign_on_service.add_domain(db_session, s["tenant"].id, s["customer"].id, "acme.example.com")
    domain.verified_at = datetime.now(timezone.utc)
    db_session.commit()

    # What the customer's administrator gives their identity provider: our entity ID and reply URL.
    urls = saml_service.sp_urls("https://openclear.bank.example", connection)
    admin = _keycloak_admin()
    made = admin.post("/clients", json={
        "clientId": urls["entity_id"], "protocol": "saml", "enabled": True, "redirectUris": ["*"],
        "attributes": {"saml.client.signature": "false", "saml.server.signature": "true", "saml.assertion.signature": "true",
                       "saml.authnstatement": "true", "saml_force_name_id_format": "true", "saml_name_id_format": "email",
                       "saml.encrypt": "false", "saml_assertion_consumer_url_post": urls["acs"]},
        "protocolMappers": [{"name": "groups", "protocol": "saml", "protocolMapper": "saml-group-membership-mapper",
                             "config": {"attribute.name": "groups", "full.path": "false", "single": "false"}}],
    })
    assert made.status_code == 201, made.text

    start = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(s["tenant"].id), "next": "/ui/"},
                       follow_redirects=False)
    assert start.status_code == 303 and start.headers["location"].startswith(ISSUER), start.text[:300]
    browser = httpx.Client(follow_redirects=False, timeout=30)
    page = browser.get(start.headers["location"])
    action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', page.text).group(1))
    cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)
    answer = browser.post(urljoin(start.headers["location"], action), headers={"Cookie": cookies}, data={
        "username": os.environ["OPENCLEAR_TEST_KEYCLOAK_EMAIL"], "password": os.environ["OPENCLEAR_TEST_KEYCLOAK_PASSWORD"],
        "credentialId": ""})
    assert answer.status_code == 200 and "SAMLResponse" in answer.text, answer.text[-500:]
    fields = dict(re.findall(r'name="(SAMLResponse|RelayState)"\s+value="([^"]*)"', answer.text))
    posted = client.post(urlsplit(urls["acs"]).path, data={k: html.unescape(v) for k, v in fields.items()}, follow_redirects=False)
    assert posted.status_code == 303 and posted.headers["location"] == "/ui/", posted.text[:500]
    claims = decode_token(client.cookies.get("access_token"))
    assert claims["customer_id"] == str(s["customer"].id) and claims["cfd"] == 1



def test_a_saml_connections_key_is_replaced_without_an_outage(client, db_session, tenant_factory, monkeypatch):
    """Issue #21 against a real identity provider that checks our request signatures: sign-in
    works before, while both certificates are published, and after the switch."""
    from openclear.auth import saml_service
    from openclear.domain.sso_connection import SsoProtocol
    from openclear.services import sso_service

    monkeypatch.setattr(get_settings(), "oidc_allow_private_hosts", True)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://openclear.bank.example")
    s = _sign_on_setup(db_session, tenant_factory, "kc-rekey")
    descriptor = httpx.get(f"{ISSUER}/protocol/saml/descriptor").text
    certificate = re.search(r"<(?:ds:|dsig:)?X509Certificate>([^<]+)<", descriptor).group(1).strip()
    connection = customer_sign_on_service.create(db_session, s["tenant"].id, s["customer"].id, SsoConnectionInput(
        provider=SsoProvider.KEYCLOAK, protocol=SsoProtocol.SAML, display_name="Acme Keycloak SAML", issuer=ISSUER,
        client_id=None, client_secret=None, groups_claim_name="groups", auto_provision=True,
        saml_sso_url=f"{ISSUER}/protocol/saml", saml_idp_certificate=f"-----BEGIN CERTIFICATE-----\n{certificate}\n-----END CERTIFICATE-----"))
    customer_sign_on_service.add_mapping(db_session, s["tenant"].id, s["customer"].id, connection.id, external_group="acme-staff",
                                         security_group_id=s["clerk"].id)
    domain = customer_sign_on_service.add_domain(db_session, s["tenant"].id, s["customer"].id, "acme.example.com")
    domain.verified_at = datetime.now(timezone.utc)
    db_session.commit()

    urls = saml_service.sp_urls("https://openclear.bank.example", connection)
    admin = _keycloak_admin()

    def give_keycloak(cert_pem: str) -> None:
        body = saml_service._pem_body(cert_pem)
        found = admin.get("/clients", params={"clientId": urls["entity_id"]}).json()
        attributes = {"saml.client.signature": "true", "saml.signing.certificate": body, "saml.server.signature": "true",
                      "saml.assertion.signature": "true", "saml.authnstatement": "true", "saml_force_name_id_format": "true",
                      "saml_name_id_format": "email", "saml.encrypt": "false", "saml_assertion_consumer_url_post": urls["acs"]}
        client_json = {"clientId": urls["entity_id"], "protocol": "saml", "enabled": True, "redirectUris": ["*"], "attributes": attributes,
                       "protocolMappers": [{"name": "groups", "protocol": "saml", "protocolMapper": "saml-group-membership-mapper",
                                            "config": {"attribute.name": "groups", "full.path": "false", "single": "false"}}]}
        if found:
            assert admin.put(f"/clients/{found[0]['id']}", json={**found[0], "attributes": {**found[0]["attributes"], **attributes}}).status_code == 204
        else:
            assert admin.post("/clients", json=client_json).status_code == 201

    def signs_in() -> bool:
        client.cookies.clear()
        start = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(s["tenant"].id), "next": "/ui/"},
                           follow_redirects=False)
        browser = httpx.Client(follow_redirects=False, timeout=30)
        page = browser.get(start.headers["location"])
        form = re.search(r'<form[^>]+action="([^"]+)"', page.text)
        if form is None:  # Keycloak refused our request (a signature it can't verify)
            return False
        cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)
        answer = browser.post(urljoin(start.headers["location"], html.unescape(form.group(1))), headers={"Cookie": cookies}, data={
            "username": os.environ["OPENCLEAR_TEST_KEYCLOAK_EMAIL"], "password": os.environ["OPENCLEAR_TEST_KEYCLOAK_PASSWORD"],
            "credentialId": ""})
        fields = dict(re.findall(r'name="(SAMLResponse|RelayState)"\s+value="([^"]*)"', answer.text))
        if "SAMLResponse" not in fields:
            return False
        posted = client.post(urlsplit(urls["acs"]).path, data={k: html.unescape(v) for k, v in fields.items()}, follow_redirects=False)
        return posted.status_code == 303 and posted.headers["location"] == "/ui/"

    old_cert = connection.saml_sp_certificate
    give_keycloak(old_cert)
    assert signs_in()

    sso_service.rekey(db_session, connection, "start")  # both published; still signing with the old key
    db_session.commit()
    assert signs_in()

    give_keycloak(connection.saml_sp_next_certificate)  # the provider takes the new certificate...
    sso_service.rekey(db_session, connection, "finish")  # ...and OpenClear switches to it
    db_session.commit()
    assert connection.saml_sp_certificate != old_cert and signs_in()

    give_keycloak(old_cert)  # and the old key is really gone: a provider still holding it refuses us
    assert not signs_in()
