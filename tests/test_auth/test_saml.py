# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SAML 2.0 sign-on (SDK_PLAN.md phase F; auth/saml_service.py) against a simulated identity
provider that signs real assertions with its own key: sign-in started here and at the
provider, single logout, and the attacks a SAML service provider has to refuse."""

import base64
import datetime as dt
import uuid
import zlib
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from sqlalchemy import select

from openclear.domain.sso_connection import SsoConnection, SsoIdentity, SsoProtocol, SsoProvider
from openclear.domain.user import User
from openclear.services import security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from tests.conftest import TenantFactory

IDP_ENTITY = "https://idp.example.com/metadata"
IDP_SSO = "https://idp.example.com/sso"
IDP_SLO = "https://idp.example.com/slo"


class FakeIdp:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test IdP")])
        now = dt.datetime.now(dt.timezone.utc)
        self.cert = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30)).sign(self.key, hashes.SHA256())
        )
        self.cert_pem = self.cert.public_bytes(serialization.Encoding.PEM).decode()
        self.key_pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()).decode()

    @staticmethod
    def _t(delta_minutes: float = 0) -> str:
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=delta_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def response(self, *, acs, audience, email="pat@bank.example", groups=("openclear-admins",), in_response_to=None,
                 sign=True, valid_minutes=5, tamper=None) -> str:
        irt = f' InResponseTo="{in_response_to}"' if in_response_to else ""
        assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{self._t()}"><saml:Issuer>{IDP_ENTITY}</saml:Issuer><saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID><saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData NotOnOrAfter="{self._t(valid_minutes)}" Recipient="{acs}"{irt}/></saml:SubjectConfirmation></saml:Subject><saml:Conditions NotBefore="{self._t(-5)}" NotOnOrAfter="{self._t(valid_minutes)}"><saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions><saml:AuthnStatement AuthnInstant="{self._t()}" SessionIndex="_s{uuid.uuid4().hex}"><saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement><saml:AttributeStatement><saml:Attribute Name="groups">{"".join(f'<saml:AttributeValue xsi:type="xs:string">{g}</saml:AttributeValue>' for g in groups)}</saml:Attribute></saml:AttributeStatement></saml:Assertion>"""
        if sign:
            assertion = OneLogin_Saml2_Utils.add_sign(
                assertion, self.key_pem, self.cert_pem, sign_algorithm=OneLogin_Saml2_Constants.RSA_SHA256,
                digest_algorithm=OneLogin_Saml2_Constants.SHA256,
            ).decode()
        if tamper:
            assertion = assertion.replace(*tamper)
        response = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{self._t()}" Destination="{acs}"{irt}><saml:Issuer>{IDP_ENTITY}</saml:Issuer><samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>{assertion}</samlp:Response>"""
        return base64.b64encode(response.encode()).decode()

    def logout_request(self, *, slo, name_id, sign=True) -> dict:
        xml = f"""<samlp:LogoutRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_l{uuid.uuid4().hex}" Version="2.0" IssueInstant="{self._t()}" Destination="{slo}"><saml:Issuer>{IDP_ENTITY}</saml:Issuer><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id}</saml:NameID></samlp:LogoutRequest>"""
        deflated = zlib.compress(xml.encode())[2:-4]
        params = {"SAMLRequest": base64.b64encode(deflated).decode()}
        if sign:
            params["SigAlg"] = OneLogin_Saml2_Constants.RSA_SHA256
            signed = f"SAMLRequest={quote(params['SAMLRequest'], safe='')}&SigAlg={quote(params['SigAlg'], safe='')}"
            params["Signature"] = base64.b64encode(self.key.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        return params


BASE = "http://openclear.example"


@pytest.fixture
def idp(monkeypatch, engine):
    # python3-saml wants a real host name in our own URLs (not "testserver"); after `engine`,
    # which resets settings on Postgres.
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "public_base_url", BASE)
    return FakeIdp()


def _connection(db_session, tenant, idp, *, idp_initiated=False) -> SsoConnection:
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OKTA, protocol=SsoProtocol.SAML, display_name="Bank SAML", issuer=IDP_ENTITY, client_id=None,
        client_secret=None, groups_claim_name="groups", auto_provision=True, saml_sso_url=IDP_SSO, saml_slo_url=IDP_SLO,
        saml_idp_certificate=idp.cert_pem, allow_idp_initiated=idp_initiated,
    ))
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="openclear-admins", security_group_id=admin_group.id)
    db_session.commit()
    return connection


def _urls(connection):
    root = f"{BASE}/ui/sso/saml/{connection.tenant_id}/{connection.id}"
    return {"entity": f"{root}/metadata", "acs": f"{root}/acs", "slo": f"{root}/slo", "acs_path": urlsplit(f"{root}/acs").path}


def _start(client, connection, next_path="/ui/exceptions"):
    """Starts sign-in; returns the AuthnRequest's ID and the RelayState sent with it."""
    response = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(connection.tenant_id), "next": next_path},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == IDP_SSO
    query = parse_qs(location.query)
    assert "Signature" in query and query["SigAlg"] == [OneLogin_Saml2_Constants.RSA_SHA256]  # our requests are signed
    request_xml = OneLogin_Saml2_Utils.decode_base64_and_inflate(query["SAMLRequest"][0]).decode()
    request_id = request_xml.split('ID="', 1)[1].split('"', 1)[0]
    return request_id, query["RelayState"][0]


def test_metadata_and_the_settings_page(client, db_session, tenant_factory, idp):
    tenant, _account, users = tenant_factory.make(slug="saml-meta")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    created = client.post("/ui/admin/sso", follow_redirects=False, data={
        "csrf_token": csrf, "protocol": "saml", "provider": "okta", "display_name": "Okta SAML", "issuer": IDP_ENTITY,
        "saml_sso_url": IDP_SSO, "saml_idp_certificate": idp.cert_pem, "groups_claim_name": "groups",
    })
    assert created.status_code == 303, created.text
    connection = db_session.execute(select(SsoConnection).where(SsoConnection.tenant_id == tenant.id)).scalar_one()
    assert connection.protocol == SsoProtocol.SAML and connection.saml_sp_certificate.startswith("-----BEGIN CERTIFICATE")
    assert "PRIVATE KEY" not in (connection.saml_sp_private_key_encrypted or "")

    edit = client.get(f"/ui/admin/sso/{connection.id}/edit")
    assert _urls(connection)["acs"] in edit.text
    metadata = client.get(f"/ui/sso/saml/{tenant.id}/{connection.id}/metadata")
    assert metadata.status_code == 200 and f'entityID="{_urls(connection)["entity"]}"' in metadata.text
    assert _urls(connection)["acs"] in metadata.text and "X509Certificate" in metadata.text

    bad = client.post("/ui/admin/sso", data={"csrf_token": csrf, "protocol": "saml", "provider": "okta", "display_name": "x",
                                             "issuer": IDP_ENTITY, "saml_sso_url": IDP_SSO, "saml_idp_certificate": "not a cert"})
    assert bad.status_code == 422 and "valid X.509" in bad.text


def test_sign_in_started_here(client, db_session, tenant_factory, idp):
    tenant, _account, _users = tenant_factory.make(slug="saml-sp")
    connection = _connection(db_session, tenant, idp)
    urls = _urls(connection)
    request_id, relay = _start(client, connection)

    answer = client.post(urls["acs_path"], data={"SAMLResponse": idp.response(acs=urls["acs"], audience=urls["entity"],
                                                                                 in_response_to=request_id), "RelayState": relay},
                         follow_redirects=False)
    assert answer.status_code == 303, answer.text
    assert answer.headers["location"] == "/ui/exceptions" and "access_token" in answer.headers.get("set-cookie", "")
    user = db_session.execute(select(User).where(User.email == "pat@bank.example")).scalar_one()
    identity = db_session.execute(select(SsoIdentity).where(SsoIdentity.user_id == user.id)).scalar_one()
    assert identity.subject == "pat@bank.example" and identity.connection_id == connection.id


@pytest.mark.parametrize("case", ["wrong_relay", "unsolicited", "tampered", "unsigned", "wrong_audience", "expired", "other_request"])
def test_answers_that_must_be_refused(case, client, db_session, tenant_factory, idp):
    tenant, _account, _users = tenant_factory.make(slug=f"saml-bad-{case.replace('_', '-')}")
    connection = _connection(db_session, tenant, idp)
    urls = _urls(connection)
    request_id, relay = _start(client, connection)
    kwargs = dict(acs=urls["acs"], audience=urls["entity"], in_response_to=request_id)
    if case == "unsolicited":
        kwargs["in_response_to"] = None
    elif case == "tampered":
        kwargs["tamper"] = ("pat@bank.example", "mallory@bank.example")
    elif case == "unsigned":
        kwargs["sign"] = False
    elif case == "wrong_audience":
        kwargs["audience"] = "https://some-other-sp.example/metadata"
    elif case == "expired":
        kwargs["valid_minutes"] = -10
    elif case == "other_request":
        kwargs["in_response_to"] = "_someone_elses_request"
    data = {"SAMLResponse": idp.response(**kwargs), "RelayState": "something-else" if case == "wrong_relay" else relay}

    answer = client.post(urls["acs_path"], data=data, follow_redirects=False)
    assert answer.status_code == 401, (case, answer.status_code)
    assert db_session.execute(select(User).where(User.email.in_(["pat@bank.example", "mallory@bank.example"]))).first() is None


def test_sign_in_started_at_the_provider_is_opt_in_and_never_replayable(client, db_session, tenant_factory, idp):
    tenant, _account, _users = tenant_factory.make(slug="saml-idp")
    off = _connection(db_session, tenant, idp)
    urls = _urls(off)
    unsolicited = idp.response(acs=urls["acs"], audience=urls["entity"])
    assert client.post(urls["acs_path"], data={"SAMLResponse": unsolicited}, follow_redirects=False).status_code == 401

    on = _connection(db_session, tenant, idp, idp_initiated=True)
    urls = _urls(on)
    unsolicited = idp.response(acs=urls["acs"], audience=urls["entity"])
    first = client.post(urls["acs_path"], data={"SAMLResponse": unsolicited, "RelayState": "/ui/exceptions"}, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/ui/exceptions"
    client.cookies.clear()
    replay = client.post(urls["acs_path"], data={"SAMLResponse": unsolicited}, follow_redirects=False)
    assert replay.status_code == 401

    hostile = idp.response(acs=urls["acs"], audience=urls["entity"])
    sneaky = client.post(urls["acs_path"], data={"SAMLResponse": hostile, "RelayState": "//evil.example/"}, follow_redirects=False)
    assert sneaky.headers["location"] == "/ui/"  # never off-site


def test_single_logout_ends_the_persons_sessions(client, db_session, tenant_factory, idp):
    tenant, _account, _users = tenant_factory.make(slug="saml-slo")
    connection = _connection(db_session, tenant, idp)
    urls = _urls(connection)
    request_id, relay = _start(client, connection)
    client.post(urls["acs_path"], data={"SAMLResponse": idp.response(acs=urls["acs"], audience=urls["entity"],
                                                                       in_response_to=request_id), "RelayState": relay})
    assert client.get("/ui/").status_code == 200
    user = db_session.execute(select(User).where(User.email == "pat@bank.example")).scalar_one()
    version = user.token_version
    slo_path = urlsplit(urls["slo"]).path

    unsigned = client.get(slo_path, params=idp.logout_request(slo=urls["slo"], name_id="pat@bank.example", sign=False))
    assert unsigned.status_code == 400

    signed = client.get(slo_path, params=idp.logout_request(slo=urls["slo"], name_id="pat@bank.example"), follow_redirects=False)
    assert signed.status_code == 303 and signed.headers["location"].startswith(IDP_SLO + "?SAMLResponse=")
    db_session.refresh(user)
    assert user.token_version == version + 1
    assert client.get("/ui/", follow_redirects=False).status_code in (302, 303)  # signed out here too


def test_single_logout_without_an_answer_address_lands_on_our_sign_in(client, db_session, tenant_factory, idp, monkeypatch):
    """When the identity provider gives no address to answer at, sign-out stays on this site
    (REVIEW_FIXES_PLAN.md RF-D4): only an identity provider's own URL leaves it."""
    from openclear.auth import saml_service

    tenant, _account, _users = tenant_factory.make(slug="saml-slo-local")
    connection = _connection(db_session, tenant, idp)
    monkeypatch.setattr(saml_service, "process_logout_request", lambda *a, **k: (None, None))
    answer = client.get(urlsplit(_urls(connection)["slo"]).path, params={"SAMLRequest": "x"}, follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == "/ui/login"
