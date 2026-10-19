# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Platform staff's security keys (OWASP_PLAN.md OW-D23; ADR 0005): the same ceremonies as
auth/webauthn_service.py, on platform_webauthn_credential and platform_webauthn_challenge,
which belong to no bank. Platform sign-in always needs one (web/routers/platform.py)."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import webauthn
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn.helpers.structs import AuthenticatorTransport, PublicKeyCredentialDescriptor

from openclear.auth.webauthn_service import WebauthnError, refused
from openclear.config import get_settings
from openclear.domain.platform_webauthn import PlatformWebauthnChallenge, PlatformWebauthnCredential
from openclear.domain.user import User


_CHALLENGE_TTL_MINUTES = 5


def credentials(session: Session, user_id: uuid.UUID) -> list[PlatformWebauthnCredential]:
    return list(session.execute(select(PlatformWebauthnCredential).where(PlatformWebauthnCredential.user_id == user_id)).scalars())


def _descriptor(credential: PlatformWebauthnCredential) -> PublicKeyCredentialDescriptor:
    transports = [AuthenticatorTransport(t) for t in (credential.transports or [])]
    return PublicKeyCredentialDescriptor(id=credential.credential_id, transports=transports or None)


def _store_challenge(session: Session, user: User, purpose: str, challenge: bytes) -> None:
    session.execute(delete(PlatformWebauthnChallenge).where(PlatformWebauthnChallenge.user_id == user.id,
                                                            PlatformWebauthnChallenge.purpose == purpose))
    session.add(PlatformWebauthnChallenge(user_id=user.id, challenge=challenge, purpose=purpose,
                                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=_CHALLENGE_TTL_MINUTES)))
    session.flush()


def _consume_challenge(session: Session, user_id: uuid.UUID, purpose: str) -> bytes:
    row = session.execute(select(PlatformWebauthnChallenge).where(PlatformWebauthnChallenge.user_id == user_id,
                                                                  PlatformWebauthnChallenge.purpose == purpose)).scalars().first()
    if row is None:
        raise WebauthnError("No pending security-key check: start again.")
    session.delete(row)  # single use, whatever happens next
    session.flush()
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise WebauthnError("The security-key check expired: start again.")
    return row.challenge


def begin_registration(session: Session, user: User) -> str:
    settings = get_settings()
    options = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id, rp_name=settings.webauthn_rp_name, user_id=user.id.bytes, user_name=user.email,
        user_display_name=user.email, exclude_credentials=[_descriptor(c) for c in credentials(session, user.id)] or None,
    )
    _store_challenge(session, user, "registration", options.challenge)
    return webauthn.options_to_json(options)


def complete_registration(session: Session, user: User, credential: str | dict[str, Any], *, nickname: str | None = None
                          ) -> PlatformWebauthnCredential:
    settings = get_settings()
    challenge = _consume_challenge(session, user.id, "registration")
    try:
        verified = webauthn.verify_registration_response(credential=credential, expected_challenge=challenge,
                                                         expected_rp_id=settings.webauthn_rp_id,
                                                         expected_origin=settings.webauthn_origin)
    except Exception as exc:
        raise refused("Registration verification", exc) from exc
    row = PlatformWebauthnCredential(user_id=user.id, credential_id=verified.credential_id, public_key=verified.credential_public_key,
                                     sign_count=verified.sign_count, nickname=nickname)
    session.add(row)
    session.flush()
    return row


def begin_authentication(session: Session, user: User) -> str:
    settings = get_settings()
    rows = credentials(session, user.id)
    if not rows:
        raise WebauthnError("No security key is registered for this platform account.")
    options = webauthn.generate_authentication_options(rp_id=settings.webauthn_rp_id, allow_credentials=[_descriptor(c) for c in rows])
    _store_challenge(session, user, "authentication", options.challenge)
    return webauthn.options_to_json(options)


def complete_authentication(session: Session, user: User, credential: str | dict[str, Any]) -> PlatformWebauthnCredential:
    from openclear.services import security_events

    actor = {"actor_type": "user", "actor_id": user.id, "actor_label": user.email}
    settings = get_settings()
    try:
        challenge = _consume_challenge(session, user.id, "authentication")
        parsed = webauthn.helpers.parse_authentication_credential_json(credential)
        matching = [c for c in credentials(session, user.id) if c.credential_id == parsed.raw_id]
        if not matching:
            raise WebauthnError("Unrecognized security key.")
        row = matching[0]
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=settings.webauthn_rp_id,
                expected_origin=settings.webauthn_origin, credential_public_key=row.public_key,
                credential_current_sign_count=row.sign_count)
        except Exception as exc:
            raise refused("Security key verification", exc) from exc
    except WebauthnError:
        security_events.emit_detached("auth.mfa.failed", tenant_id=None, outcome="failure", reason="platform", **actor)
        raise
    row.sign_count = verified.new_sign_count
    row.last_used_at = datetime.now(timezone.utc)
    security_events.emit(session, "auth.mfa.succeeded", tenant_id=None, details={"method": "webauthn", "where": "platform"}, **actor)
    session.flush()
    return row
