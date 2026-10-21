# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""`Idempotency-Key` for API writes (SDK_PLAN.md P0), the convention Stripe and the IETF
draft (draft-ietf-httpapi-idempotency-key-header) use.

A caller that sends `Idempotency-Key: <a unique value of its own>` on a POST, PUT, PATCH
or DELETE under /api/v1 can retry it after a timeout or a dropped connection and be sure
the work happens once:

- the first request runs, and its response is stored against the key;
- a retry with the same key and the same request gets that stored response again, with
  `Idempotency-Replayed: true`;
- the same key with a different request (method, path, query or body) is refused with
  422, since that's a bug in the caller;
- a retry that arrives while the first is still running gets 409, to try again shortly;
- a 5xx response isn't stored, so a retry after a server error runs again.

Keys are scoped to the caller (the person, or the API client's service identity) within
their bank, and kept for `idempotency_key_hours` (24). A request without the header is
unaffected. Requests that don't carry a valid bearer token, and the sign-in endpoints,
pass straight through: the route's own authentication answers them as usual.
"""

import hashlib
import uuid

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from openclear.auth.security import decode_token
from openclear.db.session import get_db
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.idempotency_record import IdempotencyRecord

HEADER = "idempotency-key"
_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SKIPPED_PREFIXES = ("/api/v1/auth/", "/api/v1/oauth/")
_MAX_KEY_LENGTH = 255
_MAX_STORED_BYTES = 5 * 1024 * 1024
_ACT_AS_HEADERS = ("openclear-act-as", "openclear-act-as-customer", "openclear-act-as-subject")


def _caller(request: Request) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(tenant, user) from a valid bearer access token, or None."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        claims = decode_token(header[7:].strip())
    except jwt.PyJWTError:
        return None
    if claims.get("type") != "access":
        return None
    try:
        return uuid.UUID(claims["tenant_id"]), uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        return None


def _open_session(app: FastAPI):
    """A session the way the routes get one, honoring the app's get_db override (the test
    suite's database), so this middleware and the request use the same database."""
    factory = app.dependency_overrides.get(get_db, get_db)
    generator = factory()
    return next(generator), generator


def _problem(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code)


def _finish(app: FastAPI, tenant_id, user_id, record_id, status_code, content_type, content) -> None:
    """Stores the response against the key, or (status_code None) deletes the key."""
    session, generator = _open_session(app)
    try:
        bind_session_to_tenant(session, tenant_id, user_id=user_id)
        record = session.get(IdempotencyRecord, record_id)
        if record is not None:
            if status_code is None:
                session.delete(record)
            else:
                record.status_code, record.content_type, record.response_body = status_code, content_type, content
            session.commit()
    finally:
        session.close()
        generator.close()


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _idempotency(request: Request, call_next):
        key = request.headers.get(HEADER)
        path = request.url.path
        if (
            key is None or request.method not in _METHODS or not path.startswith("/api/v1/")
            or path.startswith(_SKIPPED_PREFIXES)
        ):
            return await call_next(request)
        key = key.strip()
        if not key or len(key) > _MAX_KEY_LENGTH:
            return _problem(400, f"Idempotency-Key must be 1 to {_MAX_KEY_LENGTH} characters.")
        caller = _caller(request)
        if caller is None:
            return await call_next(request)
        tenant_id, user_id = caller

        body = await request.body()
        # Who an API client acts for is part of the request (FULL_REVIEW_PLAN.md FR2, R10): the
        # same key and body for two people are two different requests.
        # Only when one is sent, so a request without them fingerprints as it always has.
        acting_for = [h.encode() + b"=" + request.headers[h].encode() for h in _ACT_AS_HEADERS if h in request.headers]
        request_hash = hashlib.sha256(
            b"\n".join([request.method.encode(), path.encode(), request.url.query.encode(), *acting_for, body])
        ).hexdigest()

        session, generator = _open_session(request.app)
        try:
            bind_session_to_tenant(session, tenant_id, user_id=user_id)
            existing = session.execute(select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key,
            )).scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    return _problem(422, "This Idempotency-Key was already used with a different request.")
                if existing.status_code is None:
                    return _problem(409, "A request with this Idempotency-Key is still being processed; retry shortly.")
                return Response(
                    existing.response_body or b"", status_code=existing.status_code, media_type=existing.content_type,
                    headers={"Idempotency-Replayed": "true"},
                )
            record = IdempotencyRecord(tenant_id=tenant_id, user_id=user_id, key=key, request_hash=request_hash)
            session.add(record)
            try:
                session.commit()
            except IntegrityError:  # the same key arrived twice at once: the other one is running
                session.rollback()
                return _problem(409, "A request with this Idempotency-Key is still being processed; retry shortly.")
            record_id = record.id
        finally:
            session.close()
            generator.close()

        try:
            response = await call_next(request)
            content = b"".join([chunk async for chunk in response.body_iterator])
        except BaseException:
            # An unhandled error becomes a 500 further out; don't leave the key looking
            # in flight, or every retry would get 409 until it expired.
            _finish(request.app, tenant_id, user_id, record_id, None, None, None)
            raise
        if response.status_code >= 500 or len(content) > _MAX_STORED_BYTES:
            _finish(request.app, tenant_id, user_id, record_id, None, None, None)  # not replayable: a retry runs again
        else:
            _finish(request.app, tenant_id, user_id, record_id, response.status_code, response.headers.get("content-type"), content)

        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content, status_code=response.status_code, headers=headers, media_type=response.media_type)
