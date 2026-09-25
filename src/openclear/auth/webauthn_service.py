# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import webauthn
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn.helpers.structs import AuthenticatorTransport, PublicKeyCredentialDescriptor

from openclear.config import get_settings
from openclear.domain.user import User
from openclear.domain.webauthn_challenge import WebauthnChallenge, WebauthnChallengePurpose
from openclear.domain.webauthn_credential import WebauthnCredential
from openclear.repositories.webauthn_credential_repo import WebauthnCredentialRepository

_CHALLENGE_TTL_MINUTES = 5


class WebauthnError(Exception):
    """Raised for any ceremony failure (expired/missing challenge, bad signature, unknown
    credential) — callers map this to a 400/401, never a 500, since these are all
    legitimate client-side outcomes (stale challenge, wrong key, replayed request)."""


def _descriptor(credential: WebauthnCredential) -> PublicKeyCredentialDescriptor:
    transports = [AuthenticatorTransport(t) for t in (credential.transports or [])]
    return PublicKeyCredentialDescriptor(id=credential.credential_id, transports=transports or None)


def _store_challenge(
    session: Session, user: User, tenant_id: uuid.UUID, purpose: WebauthnChallengePurpose, challenge: bytes
) -> WebauthnChallenge:
    # Only one pending challenge per (user, purpose) at a time — starting a new ceremony
    # invalidates any prior unfinished one for the same purpose, rather than letting them
    # accumulate or leaving ambiguity about which challenge a late response answers.
    session.execute(
        delete(WebauthnChallenge).where(WebauthnChallenge.user_id == user.id, WebauthnChallenge.purpose == purpose)
    )
    row = WebauthnChallenge(
        tenant_id=tenant_id,
        user_id=user.id,
        challenge=challenge,
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=_CHALLENGE_TTL_MINUTES),
    )
    session.add(row)
    session.flush()
    return row


def _consume_challenge(session: Session, user_id: uuid.UUID, purpose: WebauthnChallengePurpose) -> bytes:
    stmt = select(WebauthnChallenge).where(WebauthnChallenge.user_id == user_id, WebauthnChallenge.purpose == purpose)
    row = session.execute(stmt).scalars().first()
    if row is None:
        raise WebauthnError("No pending WebAuthn ceremony for this user — start it again.")

    # Delete immediately (single-use) regardless of expiry outcome below — an expired
    # challenge must not be retryable, and a used one must never be replayable.
    session.delete(row)
    session.flush()

    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise WebauthnError("WebAuthn challenge expired — start the ceremony again.")

    return row.challenge


def begin_registration(session: Session, user: User, tenant_id: uuid.UUID) -> str:
    """WebAuthn credentials are still registered per tenant-membership, not once for the
    whole identity (see docs/ARCHITECTURE.md, "Authentication and sessions") — a user with memberships in two tenants registers a key separately in each."""
    settings = get_settings()
    existing = WebauthnCredentialRepository(session, tenant_id).list(user_id=user.id)

    options = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.email,
        exclude_credentials=[_descriptor(c) for c in existing] or None,
    )

    _store_challenge(session, user, tenant_id, WebauthnChallengePurpose.REGISTRATION, options.challenge)
    return webauthn.options_to_json(options)


def complete_registration(
    session: Session, user: User, tenant_id: uuid.UUID, credential: str | dict[str, Any], *, nickname: str | None = None
) -> WebauthnCredential:
    settings = get_settings()
    expected_challenge = _consume_challenge(session, user.id, WebauthnChallengePurpose.REGISTRATION)

    try:
        verified = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
        )
    except Exception as exc:  # webauthn raises its own exception hierarchy; normalize to WebauthnError
        raise WebauthnError(f"Registration verification failed: {exc}") from exc

    repo = WebauthnCredentialRepository(session, tenant_id)
    credential_row = WebauthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=verified.aaguid,
        nickname=nickname,
    )
    repo.add(credential_row)
    session.flush()
    return credential_row


def begin_authentication(session: Session, user: User, tenant_id: uuid.UUID) -> str:
    settings = get_settings()
    credentials = WebauthnCredentialRepository(session, tenant_id).list(user_id=user.id)
    if not credentials:
        raise WebauthnError("This user has no registered security keys.")

    options = webauthn.generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=[_descriptor(c) for c in credentials],
    )

    _store_challenge(session, user, tenant_id, WebauthnChallengePurpose.AUTHENTICATION, options.challenge)
    return webauthn.options_to_json(options)


def complete_authentication(
    session: Session, user: User, tenant_id: uuid.UUID, credential: str | dict[str, Any]
) -> WebauthnCredential:
    settings = get_settings()
    expected_challenge = _consume_challenge(session, user.id, WebauthnChallengePurpose.AUTHENTICATION)

    parsed = webauthn.helpers.parse_authentication_credential_json(credential)
    repo = WebauthnCredentialRepository(session, tenant_id)
    matching = [c for c in repo.list(user_id=user.id) if c.credential_id == parsed.raw_id]
    if not matching:
        raise WebauthnError("Unrecognized credential for this user.")
    credential_row = matching[0]

    try:
        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=credential_row.public_key,
            credential_current_sign_count=credential_row.sign_count,
        )
    except Exception as exc:
        raise WebauthnError(f"Authentication verification failed: {exc}") from exc

    # Sign-count regression is the standard signal a credential was cloned — reject it
    # rather than silently accepting, even though we already verified the signature.
    if verified.new_sign_count != 0 and verified.new_sign_count <= credential_row.sign_count:
        raise WebauthnError("Authenticator sign count did not increase — possible cloned credential.")

    credential_row.sign_count = verified.new_sign_count
    credential_row.last_used_at = datetime.now(timezone.utc)
    session.flush()
    return credential_row


def user_has_webauthn_credentials(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return bool(WebauthnCredentialRepository(session, tenant_id).list(user_id=user_id))


def list_credentials(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[WebauthnCredential]:
    return WebauthnCredentialRepository(session, tenant_id).list(user_id=user_id)


def delete_credential(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, credential_id: uuid.UUID) -> bool:
    repo = WebauthnCredentialRepository(session, tenant_id)
    row = repo.get(credential_id)
    if row is None or row.user_id != user_id:
        return False
    session.delete(row)
    session.flush()
    return True
