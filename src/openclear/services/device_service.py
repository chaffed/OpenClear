# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""New-device sign-in emails (OWASP_PLAN.md OW-D30; ASVS 5.0 6.3.5).

A browser is known by a long-lived random cookie (`__Host-device` over HTTPS) of which only the
SHA-256 is stored against the person (user_device). A sign-in from a browser without a known
cookie emails the person, always, with the browser, address and time. A person's first device
ever is recorded without an email, or everyone would be emailed at their first sign-in after
1.24. Embedded sessions don't come through here: the online banking site is the device."""

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.user import User
from openclear.domain.user_device import UserDevice

COOKIE = "device"
COOKIE_DAYS = 400  # browsers cap a cookie's life near this


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def note_sign_in(db: Session, request: Request, response, user: User, *, bank_name: str) -> bool:
    """Records this browser for the person, emailing them if it's new. Commits. Returns whether
    it was new."""
    from openclear.services import notification_service, session_service
    from openclear.web import request_context
    from openclear.web.security import _secure_cookies, cookie_name, read_cookie, token_shaped

    now = datetime.now(timezone.utc)
    token = read_cookie(request, COOKIE)
    token = token if token_shaped(token) else secrets.token_urlsafe(32)
    device_hash = _hash(token)
    context = request_context.current()
    user_agent = (context.user_agent if context else None) or (request.headers.get("user-agent") or "")[:300] or None
    known = db.execute(select(UserDevice).where(UserDevice.user_id == user.id, UserDevice.device_hash == device_hash)).scalar_one_or_none()
    new = known is None
    if known is not None:
        known.last_seen_at = now
    else:
        first_ever = db.execute(select(UserDevice.id).where(UserDevice.user_id == user.id).limit(1)).first() is None
        db.add(UserDevice(user_id=user.id, device_hash=device_hash, user_agent=user_agent, first_seen_at=now, last_seen_at=now))
        if not first_ever:
            notification_service.notify_new_device(
                db, user, browser=session_service.describe_browser(user_agent),
                address=(context.client_ip if context else None) or "an unknown address", at=now, bank_name=bank_name)
    db.commit()
    response.set_cookie(cookie_name(COOKIE), token, httponly=True, secure=_secure_cookies(), samesite="lax", path="/",
                        max_age=COOKIE_DAYS * 24 * 3600)
    return new
