# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Embedding OpenClear's pages in a bank's online banking (SDK_PLAN.md P2).

1. The bank lists the https origins of its online banking that may frame OpenClear
   (Settings → Embedding). Nothing else may ever frame it.
2. The online banking server, acting for the person signed in to it (token exchange or
   OpenClear-Act-As), asks for a **launch**: `POST /api/v1/embed/launch` with the page to
   open and the origin that will frame it. It gets a URL with a one-time code, good for a
   minute.
3. The online banking page opens that URL in a frame (`@openclear/embed` does this). The code
   starts an **embedded session** for that person: the same identity, membership and
   permissions they'd have signing in, with the frame's origin in the session. Its pages
   drop the navigation, may be framed by that origin only (and only while the bank still
   lists it), and talk to it with postMessage (resize, navigation, session ended).

Cookies for a frame on another site are `SameSite=None; Secure; Partitioned`
(web/security.py), kept in a jar for that one online banking site. Hosting OpenClear on a
subdomain of the online banking domain avoids third-party cookie limits altogether."""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.domain.embed_launch import EmbedLaunch
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User

LAUNCH_SECONDS = 60
_CODE = re.compile(r"^oclaunch_([0-9a-f]{32})_([A-Za-z0-9_-]{30,})$")
MAX_ORIGINS = 20


class EmbedError(UserFacingError):
    """Refused; the message is fit to show the online banking developer."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def normalize_origin(value: str) -> str:
    """`https://host[:port]`, nothing else (no path, query or wildcard)."""
    try:
        parts = urlsplit((value or "").strip())
        port = parts.port
    except ValueError:
        raise EmbedError(f"{(value or '').strip()!r} isn't an https origin like https://banking.example.com") from None
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment or "*" in value:
        raise EmbedError(f"{value.strip()!r} isn't an https origin like https://banking.example.com")
    if parts.username or parts.password:
        raise EmbedError(f"{value.strip()!r} mustn't contain credentials")
    # The origin goes into the Content-Security-Policy header: a host is letters, digits, dots
    # and hyphens (punycode for international names), nothing that could end a directive.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", parts.hostname.lower()):
        raise EmbedError(f"{value.strip()!r} has characters a host name can't have")
    port = f":{port}" if port and port != 443 else ""
    return f"https://{parts.hostname.lower()}{port}"


def parse_origins(text: str | None) -> list[str]:
    origins: list[str] = []
    for line in (text or "").replace(",", "\n").splitlines():
        if line.strip():
            origin = normalize_origin(line)
            if origin not in origins:
                origins.append(origin)
    if len(origins) > MAX_ORIGINS:
        raise EmbedError(f"At most {MAX_ORIGINS} origins.")
    return origins


def allowed_origins(session: Session, tenant_id: uuid.UUID) -> list[str]:
    tenant = session.get(Tenant, tenant_id)
    try:
        return parse_origins(tenant.embed_allowed_origins if tenant else None)
    except EmbedError:
        return []


def origin_allowed(session: Session, tenant_id: uuid.UUID, origin: str) -> bool:
    return origin in allowed_origins(session, tenant_id)


def set_allowed_origins(session: Session, tenant_id: uuid.UUID, text: str) -> list[str]:
    origins = parse_origins(text)
    session.get(Tenant, tenant_id).embed_allowed_origins = "\n".join(origins) or None
    session.flush()
    return origins


def create_launch(
    session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID, membership_id: uuid.UUID, api_client_id: uuid.UUID | None,
    path: str, parent_origin: str,
) -> str:
    """A one-time launch code for the person (the caller's acting context)."""
    from openclear.web.security import safe_next_path

    origin = normalize_origin(parent_origin)
    if not origin_allowed(session, tenant_id, origin):
        raise EmbedError(f"{origin} isn't one of this organization's embedding origins (Settings → Embedding).")
    target = safe_next_path(path, default="")
    if not target.startswith("/ui/") or target.startswith(("/ui/login", "/ui/embed", "/ui/auth")):
        raise EmbedError("path must be a page under /ui/, such as /ui/exceptions.")
    code = f"oclaunch_{tenant_id.hex}_{secrets.token_urlsafe(32)}"
    session.add(EmbedLaunch(
        tenant_id=tenant_id, code_hash=_hash(code), user_id=user_id, membership_id=membership_id, api_client_id=api_client_id,
        path=target[:500], parent_origin=origin, expires_at=datetime.now(timezone.utc) + timedelta(seconds=LAUNCH_SECONDS),
    ))
    session.flush()
    return code


def tenant_of(code: str) -> uuid.UUID | None:
    match = _CODE.match(code or "")
    return uuid.UUID(match.group(1)) if match else None


def redeem(session: Session, code: str) -> tuple[EmbedLaunch, User, TenantMembership]:
    """Uses a launch code (once). The caller has bound the session to tenant_of(code)."""
    from sqlalchemy import select

    launch = session.execute(select(EmbedLaunch).where(EmbedLaunch.code_hash == _hash(code))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if launch is None or launch.used_at is not None:
        raise EmbedError("This link has already been used, or isn't valid.")
    expires = launch.expires_at if launch.expires_at.tzinfo else launch.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        raise EmbedError("This link has expired.")
    from openclear.db.claims import AlreadyUsed, claim_once

    try:
        claim_once(session, launch, when=now)
    except AlreadyUsed:
        raise EmbedError("This link has already been used, or isn't valid.") from None
    user, membership = session.get(User, launch.user_id), session.get(TenantMembership, launch.membership_id)
    if user is None or not user.is_active or membership is None or not membership.is_active or membership.user_id != user.id:
        raise EmbedError("That person no longer has access.")
    if not origin_allowed(session, launch.tenant_id, launch.parent_origin):
        raise EmbedError("That site may no longer embed OpenClear.")
    session.flush()
    return launch, user, membership
