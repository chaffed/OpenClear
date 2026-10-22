# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The decision feed (SDK_PLAN.md P0; services/decision_feed_service.py): every pay or
return decision, in order, for a core to act on."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from openclear.api.v1 import customer_filter
from openclear.api.v1.files import ANY_FILE, CSV_FILE
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import audit_log_service, decision_feed_service, plugin_service

router = APIRouter(prefix="/decisions", tags=["decision-feed"])
_read = require_permission("exception:read")


class Acknowledgement(BaseModel):
    consumer: str = Field(..., max_length=decision_feed_service.MAX_CONSUMER_NAME, description='Who processed them, e.g. "core"')
    through_sequence: int = Field(..., ge=0)


@router.get("")
def decision_feed(
    request: Request,
    response: Response,
    after: int = Query(0, ge=0, description="Return decisions after this sequence number"),
    limit: int = Query(500, ge=1, le=1000),
    customer_number: str | None = customer_filter.CUSTOMER_NUMBER,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(_read),
) -> list[dict]:
    """Decisions with `sequence` greater than `after`, oldest first. Keep the last sequence
    you processed and pass it as `after` next time. When a full page comes back, a
    `Link: rel="next"` header points at the next one. With `customer_number`, only that
    customer's decisions; the sequence numbers are still the bank's, so expect gaps."""
    feed = decision_feed_service.read_feed(db, ctx.tenant_id, after=after, limit=limit,
                                           customer_id=customer_filter.resolve(db, ctx, customer_number))
    response.headers["X-Latest-Sequence"] = str(decision_feed_service.latest_sequence(db, ctx.tenant_id))
    if len(feed) == limit:
        response.headers["Link"] = f'<{request.url.include_query_params(after=feed[-1]["sequence"], limit=limit)}>; rel="next"'
    return feed


@router.get("/feed.csv", **CSV_FILE)
def decision_feed_csv(
    after: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_read),
) -> Response:
    """The same decisions as a CSV file, for a core that takes files."""
    feed = decision_feed_service.read_feed(db, ctx.tenant_id, after=after, limit=limit, customer_id=ctx.customer_id)
    last = feed[-1]["sequence"] if feed else after
    return Response(
        decision_feed_service.to_csv(feed), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="decisions-{after + 1}-{last}.csv"'},
    )


@router.get("/file", **ANY_FILE)
def decision_file(
    layout: str = Query("openclear_csv", description="A decision layout: openclear_csv, or one a plugin provides that's turned on"),
    after: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_read),
) -> Response:
    """The same decisions as a file in the layout a core takes back. Layouts beyond the
    built-in CSV come from plugins, turned on per organization (Settings → Plugins)."""
    try:
        chosen = plugin_service.require_enabled(db, ctx.tenant_id, "decision_layouts", layout, what="decision layout")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    from openclear.networks.ach.returns_file import ReturnsFileError
    from openclear.services import decision_outbox_service

    feed = decision_feed_service.read_feed(db, ctx.tenant_id, after=after, limit=limit, customer_id=ctx.customer_id)
    last = feed[-1]["sequence"] if feed else after
    try:
        content = chosen.write(feed, decision_outbox_service.layout_settings(db, ctx.tenant_id, layout))
    except ReturnsFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return Response(
        content, media_type=chosen.media_type,
        headers={"Content-Disposition": f'attachment; filename="decisions-{after + 1}-{last}.{chosen.extension}"',
                 "X-Last-Sequence": str(last)},
    )


@router.post("/acknowledge")
def acknowledge(payload: Acknowledgement, db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> dict:
    """Confirms `consumer` has processed every decision through `through_sequence`. It
    only moves forward."""
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The decision feed is acknowledged bank-wide")
    try:
        cursor = decision_feed_service.acknowledge(
            db, ctx.tenant_id, consumer=payload.consumer, through_sequence=payload.through_sequence, user_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="decision_feed.acknowledge",
        summary=f"{cursor.consumer} acknowledged decisions through {cursor.through_sequence}", resource_type="decision_feed",
    )
    db.commit()
    return {"consumer": cursor.consumer, "through_sequence": cursor.through_sequence}


@router.get("/consumers")
def feed_consumers(db: Session = Depends(get_db), ctx: TenantContext = Depends(_read)) -> list[dict]:
    """Each consumer's acknowledged position and how many decisions it's behind."""
    return decision_feed_service.consumers(db, ctx.tenant_id)
