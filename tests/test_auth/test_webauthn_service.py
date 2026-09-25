# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.auth.webauthn_service import (
    WebauthnError,
    begin_authentication,
    begin_registration,
    complete_authentication,
    complete_registration,
    delete_credential,
    list_credentials,
    user_has_webauthn_credentials,
)
from tests.test_auth.webauthn_helpers import FakeAuthenticator


def _rp_settings():
    from openclear.config import get_settings

    settings = get_settings()
    return settings.webauthn_rp_id, settings.webauthn_origin


def test_register_then_authenticate_round_trip(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-roundtrip")
    user = users["preparer"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    options_json = begin_registration(db_session, user, tenant.id)
    db_session.commit()
    options = webauthn.helpers.parse_registration_options_json(options_json)
    credential = fake.create_registration_credential(options.challenge)

    row = complete_registration(db_session, user, tenant.id, credential, nickname="YubiKey 5")
    db_session.commit()

    assert row.nickname == "YubiKey 5"
    assert user_has_webauthn_credentials(db_session, tenant.id, user.id)

    auth_options_json = begin_authentication(db_session, user, tenant.id)
    db_session.commit()
    auth_options = webauthn.helpers.parse_authentication_options_json(auth_options_json)
    assertion = fake.create_authentication_credential(auth_options.challenge)

    authenticated_row = complete_authentication(db_session, user, tenant.id, assertion)
    db_session.commit()

    assert authenticated_row.id == row.id
    assert authenticated_row.sign_count == 1
    assert authenticated_row.last_used_at is not None


def test_registration_rejects_wrong_challenge(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-wrong-challenge")
    user = users["preparer"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    begin_registration(db_session, user, tenant.id)
    db_session.commit()

    wrong_challenge = webauthn.helpers.generate_challenge()
    credential = fake.create_registration_credential(wrong_challenge)

    with pytest.raises(WebauthnError):
        complete_registration(db_session, user, tenant.id, credential)


def test_authentication_fails_without_prior_registration(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="webauthn-no-creds")
    user = users["preparer"]

    with pytest.raises(WebauthnError):
        begin_authentication(db_session, user, tenant.id)


def test_authentication_rejects_replayed_challenge(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-replay")
    user = users["preparer"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    options_json = begin_registration(db_session, user, tenant.id)
    db_session.commit()
    options = webauthn.helpers.parse_registration_options_json(options_json)
    complete_registration(db_session, user, tenant.id, fake.create_registration_credential(options.challenge))
    db_session.commit()

    auth_options_json = begin_authentication(db_session, user, tenant.id)
    db_session.commit()
    auth_options = webauthn.helpers.parse_authentication_options_json(auth_options_json)
    assertion = fake.create_authentication_credential(auth_options.challenge)

    complete_authentication(db_session, user, tenant.id, assertion)
    db_session.commit()

    # The challenge was consumed by the first verification — replaying the same
    # assertion must fail because there's no longer a pending challenge to check it against.
    with pytest.raises(WebauthnError):
        complete_authentication(db_session, user, tenant.id, assertion)


def test_starting_a_new_ceremony_invalidates_the_previous_unfinished_one(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-invalidate")
    user = users["preparer"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    first_options_json = begin_registration(db_session, user, tenant.id)
    db_session.commit()
    first_options = webauthn.helpers.parse_registration_options_json(first_options_json)
    stale_credential = fake.create_registration_credential(first_options.challenge)

    # Starting a second registration ceremony must invalidate the first's challenge.
    begin_registration(db_session, user, tenant.id)
    db_session.commit()

    with pytest.raises(WebauthnError):
        complete_registration(db_session, user, tenant.id, stale_credential)


def test_list_and_delete_credential(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-list-delete")
    user = users["preparer"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    options_json = begin_registration(db_session, user, tenant.id)
    db_session.commit()
    options = webauthn.helpers.parse_registration_options_json(options_json)
    row = complete_registration(db_session, user, tenant.id, fake.create_registration_credential(options.challenge))
    db_session.commit()

    listed = list_credentials(db_session, tenant.id, user.id)
    assert [c.id for c in listed] == [row.id]

    deleted = delete_credential(db_session, tenant.id, user.id, row.id)
    db_session.commit()
    assert deleted is True
    assert list_credentials(db_session, tenant.id, user.id) == []


def test_delete_credential_scoped_to_owning_user(db_session, tenant_factory):
    import webauthn

    tenant, _account, users = tenant_factory.make(slug="webauthn-delete-scope")
    owner = users["preparer"]
    other_user = users["approver"]
    rp_id, origin = _rp_settings()
    fake = FakeAuthenticator(rp_id, origin)

    options_json = begin_registration(db_session, owner, tenant.id)
    db_session.commit()
    options = webauthn.helpers.parse_registration_options_json(options_json)
    row = complete_registration(db_session, owner, tenant.id, fake.create_registration_credential(options.challenge))
    db_session.commit()

    # A different user in the same tenant must not be able to delete owner's credential.
    deleted = delete_credential(db_session, tenant.id, other_user.id, row.id)
    assert deleted is False
    assert list_credentials(db_session, tenant.id, owner.id) != []
