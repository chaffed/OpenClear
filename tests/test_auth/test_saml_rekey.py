# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Replacing a SAML connection's signing key without a sign-in outage (issue #21): the new
certificate is published beside the old, requests keep the old key until an admin finishes, and
the change goes through dual control where the bank asks for it."""

import base64
from urllib.parse import parse_qs, urlsplit

import pytest
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from sqlalchemy import select

from openclear.auth import saml_service
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.change_request import ChangeRequest
from openclear.services import change_request_service, security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_auth.test_saml import BASE, _connection, _start, _urls, idp  # noqa: F401 -- idp is a fixture
from tests.test_web.test_staff_and_operator import _login


def _old_2048_key(db_session, connection):
    """As a connection made before 1.22.1 has."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime as dt

    from openclear.auth.crypto import encrypt_secret

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "old")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))
    connection.saml_sp_certificate = cert.public_bytes(serialization.Encoding.PEM).decode()
    connection.saml_sp_private_key_encrypted = encrypt_secret(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
    db_session.commit()


def _signed_with(client, connection, certificate_pem) -> bool:
    """Whether the AuthnRequest a sign-in sends is signed with this certificate's key."""
    response = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(connection.tenant_id), "next": "/ui/"},
                          follow_redirects=False)
    query = urlsplit(response.headers["location"]).query
    signed = "&".join(part for part in query.split("&") if not part.startswith("Signature="))
    signature = base64.b64decode(parse_qs(query)["Signature"][0])
    return OneLogin_Saml2_Utils.validate_binary_sign(signed, signature, certificate_pem)


def _metadata_certs(client, connection) -> int:
    return client.get(f"/ui/sso/saml/{connection.tenant_id}/{connection.id}/metadata").text.count("<ds:X509Certificate>")


def _post(client, connection, step):
    return client.post(f"/ui/admin/sso/{connection.id}/signing-key", data={"csrf_token": client.cookies.get("csrf_token"), "step": step},
                       follow_redirects=False)


def test_an_old_key_is_replaced_in_steps_without_breaking_sign_in(client, db_session, tenant_factory, idp):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="saml-rekey")
    connection = _connection(db_session, tenant, idp)
    _old_2048_key(db_session, connection)
    old_cert = connection.saml_sp_certificate
    assert saml_service.key_bits(old_cert) == 2048 and _metadata_certs(client, connection) == 1

    _login(client, tenant.slug, users["admin"].email)
    page = client.get(f"/ui/admin/sso/{connection.id}/edit").text
    assert "RSA-2048 key" in page and "below the 3,072 bits" in page
    assert "error=No+new+signing+key" in _post(client, connection, "finish").headers["location"]  # nothing waiting: refused
    db_session.expire_all()
    assert connection.saml_sp_certificate == old_cert

    assert _post(client, connection, "start").status_code == 303
    db_session.expire_all()
    new_cert = connection.saml_sp_next_certificate
    assert saml_service.key_bits(new_cert) == 3072 and connection.saml_sp_rekey_started_at is not None
    assert _metadata_certs(client, connection) == 2  # both published
    assert "Replacing" in client.get(f"/ui/admin/sso/{connection.id}/edit").text
    client.cookies.clear()
    assert _signed_with(client, connection, old_cert)  # still the old key until the provider has the new one

    _login(client, tenant.slug, users["admin"].email)
    assert _post(client, connection, "finish").status_code == 303
    db_session.expire_all()
    assert connection.saml_sp_certificate == new_cert and connection.saml_sp_next_certificate is None
    assert _metadata_certs(client, connection) == 1
    client.cookies.clear()
    assert _signed_with(client, connection, new_cert) and not _signed_with(client, connection, old_cert)

    actions = [a.action for a in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()]
    assert actions.count("sso_connection.rekey") == 2


def test_sign_in_still_works_while_both_keys_are_published(client, db_session, tenant_factory, idp):  # noqa: F811
    tenant, _account, _users = tenant_factory.make(slug="saml-rekey-signin")
    connection = _connection(db_session, tenant, idp)
    from openclear.services import sso_service

    sso_service.rekey(db_session, connection, "start")
    db_session.commit()
    urls = _urls(connection)
    request_id, relay = _start(client, connection)
    answer = client.post(urls["acs_path"], data={"SAMLResponse": idp.response(acs=urls["acs"], audience=urls["entity"],
                                                                                 in_response_to=request_id), "RelayState": relay},
                         follow_redirects=False)
    assert answer.status_code == 303 and "access_token" in answer.headers.get("set-cookie", "")


def test_a_bank_that_asks_for_two_people_approves_start_and_finish(client, db_session, tenant_factory, idp):  # noqa: F811
    tenant, _account, users = tenant_factory.make(slug="saml-rekey-dc")
    tenant.sensitive_changes_need_approval = True
    connection = _connection(db_session, tenant, idp)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    second = user_service.create_user_with_membership(db_session, tenant.id, email="second@saml-rekey-dc.example.com",
                                                      password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    assert _post(client, connection, "start").status_code == 303
    db_session.expire_all()
    assert connection.saml_sp_next_certificate is None  # waiting for a second admin
    change = db_session.execute(select(ChangeRequest).where(ChangeRequest.kind == "sso_connection.rekey")).scalar_one()
    change_request_service.decide(db_session, tenant.id, change.id, approve=True, user_id=second.id)
    db_session.commit()
    db_session.expire_all()
    assert connection.saml_sp_next_certificate is not None

    # Withdrawing the new key undoes nothing risky, so it applies at once.
    assert _post(client, connection, "cancel").status_code == 303
    db_session.expire_all()
    assert connection.saml_sp_next_certificate is None


def test_a_customer_replaces_its_own_connections_key(client, db_session, tenant_factory, idp):  # noqa: F811
    from tests.items import _make_customer_with_account
    from openclear.services import sso_service
    from openclear.services.sso_service import SsoConnectionInput
    from openclear.domain.sso_connection import SsoProtocol, SsoProvider
    from tests.test_auth.test_saml import IDP_ENTITY, IDP_SSO

    tenant, _account, _users = tenant_factory.make(slug="saml-rekey-cust")
    customer, _acct = _make_customer_with_account(db_session, tenant, "SRK-1")
    customer.self_manages_sso = True
    db_session.flush()
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OKTA, protocol=SsoProtocol.SAML, display_name="Their SAML", issuer=IDP_ENTITY, client_id=None,
        client_secret=None, groups_claim_name="groups", auto_provision=False, saml_sso_url=IDP_SSO, saml_slo_url=None,
        saml_idp_certificate=idp.cert_pem, customer_id=customer.id))
    connection.managed_by = "customer"  # made by the customer's own people
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="it@saml-rekey-cust.example.com",
                                                      password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    _login(client, tenant.slug, person.email)
    from openclear.web.routers.sign_on import _BASE

    page = client.get(f"{_BASE}/{connection.id}/edit")
    assert page.status_code == 200 and "Replace the signing key" in page.text
    done = client.post(f"{_BASE}/{connection.id}/signing-key", data={"csrf_token": client.cookies.get("csrf_token"), "step": "start"},
                       follow_redirects=False)
    assert done.status_code == 303
    db_session.expire_all()
    assert connection.saml_sp_next_certificate is not None
    # Another customer's (or the bank's) connection isn't reachable this way.
    assert client.post(f"/ui/admin/sso/{connection.id}/signing-key", data={"csrf_token": client.cookies.get("csrf_token"), "step": "cancel"},
                       follow_redirects=False).status_code in (403, 404)


def test_steps_that_dont_apply_are_refused():
    class Oidc:
        protocol = "oidc"

    from openclear.services import sso_service

    with pytest.raises(ValueError, match="Only a SAML connection"):
        sso_service.rekey(None, Oidc(), "start")
