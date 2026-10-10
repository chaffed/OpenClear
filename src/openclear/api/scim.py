# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SCIM 2.0 endpoints, /scim/v2 (services/scim_service.py). Not part of /api/v1: SCIM is its
own standard, called by directories, and documented by RFC 7644 rather than our contract."""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.sso_connection import SsoConnection
from openclear.services import audit_log_service, scim_service
from openclear.services.scim_service import ScimError

router = APIRouter(prefix="/scim/v2", tags=["scim"], include_in_schema=False)
MEDIA = "application/scim+json"


def _json(body: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status_code, media_type=MEDIA)


def _error(exc: ScimError) -> JSONResponse:
    return _json(exc.body(), exc.status)


def _base(request: Request) -> str:
    return (get_settings().public_base_url or str(request.base_url)).rstrip("/")


class _Caller:
    def __init__(self, connection: SsoConnection | None, error: ScimError | None) -> None:
        self.connection, self.error = connection, error


def _caller(request: Request, db: Session = Depends(get_db)) -> _Caller:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    ids = scim_service.parse_token(token)
    if ids is None:
        _scim_refused(None, "no_token" if not token else "malformed_token")
        return _Caller(None, ScimError(401, "A SCIM bearer token is required."))
    bind_session_to_tenant(db, ids[0])
    try:
        return _Caller(scim_service.authenticate(db, token), None)
    except ScimError as exc:
        if exc.status in (401, 403):
            _scim_refused(ids[0], "invalid_token" if exc.status == 401 else "refused")
        return _Caller(None, exc)


def _scim_refused(tenant_id, reason: str) -> None:
    """A directory's SCIM request refused: a security event, sampled per source."""
    from openclear.services import security_events
    from openclear.web import request_context

    context = request_context.current()
    security_events.emit_detached("scim.auth.failed", sampled_key=f"scim|{context.client_ip if context else ''}",
                                  tenant_id=tenant_id, outcome="failure", reason=reason, actor_type="system")


def _run(caller: _Caller, db: Session, action: Callable[[SsoConnection], Response]) -> Response:
    if caller.error is not None:
        return _error(caller.error)
    try:
        return action(caller.connection)
    except ScimError as exc:
        db.rollback()
        return _error(exc)


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise ScimError(400, "The request body isn't JSON.", "invalidSyntax") from None
    if not isinstance(body, dict):
        raise ScimError(400, "The request body must be an object.", "invalidSyntax")
    return body


def _audit(db: Session, connection: SsoConnection, action: str, summary: str, resource_id: uuid.UUID) -> None:
    audit_log_service.record_action(
        db, connection.tenant_id, actor_user_id=None, channel="api", action=action,
        summary=f"{summary} (SCIM, {connection.display_name})", resource_type="scim", resource_id=resource_id,
    )


def _page(start_index: int, count: int) -> scim_service.Page:
    return scim_service.Page(max(1, start_index), max(0, min(count, scim_service.MAX_PAGE)))


def _list(resources: list[dict], total: int, page: scim_service.Page) -> JSONResponse:
    return _json({"schemas": [scim_service.LIST_SCHEMA], "totalResults": total, "startIndex": page.start_index,
                  "itemsPerPage": len(resources), "Resources": resources})


# --- Discovery -------------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
def service_provider_config(caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    return _run(caller, db, lambda _c: _json({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://github.com/chaffed/OpenClear/blob/main/README.md#authentication",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": scim_service.MAX_PAGE},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token", "description": "The connection's SCIM token", "primary": True}],
    }))


@router.get("/ResourceTypes")
def resource_types(caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    types = [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User", "endpoint": "/Users",
         "schema": scim_service.USER_SCHEMA},
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group", "endpoint": "/Groups",
         "schema": scim_service.GROUP_SCHEMA},
    ]
    return _run(caller, db, lambda _c: _list(types, len(types), scim_service.Page(1, len(types))))


@router.get("/Schemas")
def schemas(caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    def attribute(name, kind="string", required=False, multi=False):
        return {"name": name, "type": kind, "multiValued": multi, "required": required, "mutability": "readWrite", "returned": "default"}

    found = [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": scim_service.USER_SCHEMA, "name": "User", "attributes": [
            attribute("userName", required=True), attribute("externalId"), attribute("displayName"), attribute("active", "boolean"),
            attribute("name", "complex"), attribute("emails", "complex", multi=True)]},
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": scim_service.GROUP_SCHEMA, "name": "Group", "attributes": [
            attribute("displayName", required=True), attribute("externalId"), attribute("members", "complex", multi=True)]},
    ]
    return _run(caller, db, lambda _c: _list(found, len(found), scim_service.Page(1, len(found))))


# --- Users ---------------------------------------------------------------------------------


@router.get("/Users")
def list_users(request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100,  # noqa: A002,N803
               caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    def run(connection):
        page = _page(startIndex, count)
        rows, total = scim_service.list_users(db, connection, filter, page)
        return _list([scim_service.user_resource(db, u, _base(request)) for u in rows], total, page)

    return _run(caller, db, run)


@router.post("/Users")
async def create_user(request: Request, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        scim_user = scim_service.create_user(db, connection, payload)
        _audit(db, connection, "scim.user.create", f"Provisioned {scim_user.user_name}", scim_user.id)
        db.commit()
        return _json(scim_service.user_resource(db, scim_user, _base(request)), 201)

    return _run(caller, db, run)


@router.get("/Users/{user_id}")
def get_user(request: Request, user_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    return _run(caller, db, lambda c: _json(scim_service.user_resource(db, scim_service.get_user(db, c, user_id), _base(request))))


@router.put("/Users/{user_id}")
async def replace_user(request: Request, user_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        scim_user = scim_service.replace_user(db, connection, scim_service.get_user(db, connection, user_id), payload)
        _audit(db, connection, "scim.user.update", f"Updated {scim_user.user_name}" + ("" if scim_user.active else " (deactivated)"), scim_user.id)
        db.commit()
        return _json(scim_service.user_resource(db, scim_user, _base(request)))

    return _run(caller, db, run)


@router.patch("/Users/{user_id}")
async def patch_user(request: Request, user_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        scim_user = scim_service.patch_user(db, connection, scim_service.get_user(db, connection, user_id), payload)
        _audit(db, connection, "scim.user.update", f"Updated {scim_user.user_name}" + ("" if scim_user.active else " (deactivated)"), scim_user.id)
        db.commit()
        return _json(scim_service.user_resource(db, scim_user, _base(request)))

    return _run(caller, db, run)


@router.delete("/Users/{user_id}")
def delete_user(user_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    def run(connection):
        scim_user = scim_service.get_user(db, connection, user_id)
        scim_service.deactivate_user(db, connection, scim_user)
        _audit(db, connection, "scim.user.deactivate", f"Deactivated {scim_user.user_name}", scim_user.id)
        db.commit()
        return Response(status_code=204)

    return _run(caller, db, run)


# --- Groups --------------------------------------------------------------------------------


@router.get("/Groups")
def list_groups(request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100,  # noqa: A002,N803
                excludedAttributes: str | None = None,  # noqa: N803
                caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    members = "members" not in (excludedAttributes or "")

    def run(connection):
        page = _page(startIndex, count)
        rows, total = scim_service.list_groups(db, connection, filter, page)
        return _list([scim_service.group_resource(db, g, _base(request), members=members) for g in rows], total, page)

    return _run(caller, db, run)


@router.post("/Groups")
async def create_group(request: Request, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        group = scim_service.create_group(db, connection, payload)
        _audit(db, connection, "scim.group.create", f"Group {group.display_name} pushed", group.id)
        db.commit()
        return _json(scim_service.group_resource(db, group, _base(request)), 201)

    return _run(caller, db, run)


@router.get("/Groups/{group_id}")
def get_group(request: Request, group_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    return _run(caller, db, lambda c: _json(scim_service.group_resource(db, scim_service.get_group(db, c, group_id), _base(request))))


@router.put("/Groups/{group_id}")
async def replace_group(request: Request, group_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        group = scim_service.replace_group(db, connection, scim_service.get_group(db, connection, group_id), payload)
        _audit(db, connection, "scim.group.update", f"Group {group.display_name} updated", group.id)
        db.commit()
        return _json(scim_service.group_resource(db, group, _base(request)))

    return _run(caller, db, run)


@router.patch("/Groups/{group_id}")
async def patch_group(request: Request, group_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    try:
        payload = await _body(request)
    except ScimError as exc:
        return _error(exc)

    def run(connection):
        group = scim_service.patch_group(db, connection, scim_service.get_group(db, connection, group_id), payload)
        _audit(db, connection, "scim.group.update", f"Group {group.display_name} updated", group.id)
        db.commit()
        return Response(status_code=204)

    return _run(caller, db, run)


@router.delete("/Groups/{group_id}")
def delete_group(group_id: str, caller: _Caller = Depends(_caller), db: Session = Depends(get_db)) -> Response:
    def run(connection):
        group = scim_service.get_group(db, connection, group_id)
        name = group.display_name
        scim_service.delete_group(db, connection, group)
        _audit(db, connection, "scim.group.delete", f"Group {name} removed", uuid.UUID(group_id))
        db.commit()
        return Response(status_code=204)

    return _run(caller, db, run)
