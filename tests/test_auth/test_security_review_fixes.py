# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Fixes from the security review of the federation, SCIM, embedding and sandbox work
(2026-10-09): open redirects through `next` paths, identity providers claiming someone else's
account by email, and sandbox sign-up on an installation with real banks."""

import pytest

from openclear.auth.oidc_service import OidcClaims
from openclear.domain.sso_connection import SsoProtocol, SsoProvider
from openclear.services import customer_service, sandbox_service, security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from openclear.web.security import safe_next_path


@pytest.mark.parametrize("path", ["/\\evil.example", "/\\/evil.example", "//evil.example", "https://evil.example",
                                  "/\t/evil.example", "/ui/\nSet-Cookie:x", "javascript:alert(1)", ""])
def test_next_paths_never_leave_the_site(path):
    assert safe_next_path(path) == "/ui/"


def test_ordinary_next_paths_are_kept():
    assert safe_next_path("/ui/exceptions?status=open#top") == "/ui/exceptions?status=open#top"


def _connection(db_session, tenant, *, customer_id=None):
    connection = sso_service.create_connection(db_session, tenant.id, SsoConnectionInput(
        provider=SsoProvider.OKTA, protocol=SsoProtocol.OIDC, display_name="IdP", issuer="https://idp.example.com", client_id="c",
        client_secret="s", groups_claim_name="groups", auto_provision=True, customer_id=customer_id,
    ))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="everyone", security_group_id=group.id)
    db_session.commit()
    return connection


def test_an_identity_provider_cant_claim_someone_elses_account(db_session, tenant_factory):
    bank, _a, bank_users = tenant_factory.make(slug="review-bank")
    other, _b, other_users = tenant_factory.make(slug="review-other")
    customer = customer_service.create_customer(db_session, bank.id, customer_service.CustomerInput(customer_number="C1", name="Acme"))
    db_session.commit()

    # A customer's own identity provider asserting the bank admin's email.
    customer_idp = _connection(db_session, bank, customer_id=customer.id)
    taken = sso_service.complete_sso_login(db_session, bank.id, customer_idp, OidcClaims(
        email=bank_users["admin"].email, subject="x", groups=["everyone"]))
    assert taken.outcome == "not_provisioned"

    # One bank's identity provider asserting a user of another bank.
    bank_idp = _connection(db_session, bank)
    foreign = sso_service.complete_sso_login(db_session, bank.id, bank_idp, OidcClaims(
        email=other_users["approver"].email, subject="y", groups=["everyone"]))
    assert foreign.outcome == "not_provisioned"

    # Still fine: the bank's own staff through the bank's own IdP, and brand-new people.
    own = sso_service.complete_sso_login(db_session, bank.id, bank_idp, OidcClaims(email=bank_users["viewer"].email, subject="z", groups=["everyone"]))
    new = sso_service.complete_sso_login(db_session, bank.id, bank_idp, OidcClaims(email="fresh@bank.example", subject="w", groups=["everyone"]))
    assert own.outcome == new.outcome == "success"


def test_scim_cant_claim_someone_elses_account(client, db_session, tenant_factory):
    from tests.test_api.test_scim import _connection as scim_connection, _user_payload

    bank, _a, _u = tenant_factory.make(slug="review-scim")
    _other, _b, other_users = tenant_factory.make(slug="review-scim-other")
    _c, headers = scim_connection(db_session, bank)
    refused = client.post("/scim/v2/Users", headers=headers, json=_user_payload(other_users["admin"].email))
    assert refused.status_code == 409 and refused.json()["scimType"] == "uniqueness"


def test_sandbox_signup_refuses_on_an_installation_with_real_banks(db_session, tenant_factory):
    tenant_factory.make(slug="a-real-bank")
    with pytest.raises(sandbox_service.SandboxError):
        sandbox_service.create(db_session, organization="Vendor", contact_email="dev@vendor.example")


@pytest.mark.parametrize("origin", ["https://a;script-src *.evil.example", "https://bank.example\nX-Injected: 1",
                                    "https://bank example.com", "https://bänk.example"])
def test_embedding_origins_cant_break_out_of_the_csp_header(origin):
    from openclear.services import embed_service

    with pytest.raises(embed_service.EmbedError):
        embed_service.normalize_origin(origin)
    assert embed_service.normalize_origin("https://xn--bnk-qla.example") == "https://xn--bnk-qla.example"
