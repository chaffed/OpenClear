# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Paging for the API's list endpoints.

Every list endpoint takes `limit` (default 500, at most 1,000) and `offset`, returns the
rows as a plain JSON list, oldest first, and adds two headers: `X-Total-Count` (rows
matching the filters) and, when there are more, a standard `Link: <...>; rel="next"`
pointing at the next page. Before 1.6 these endpoints returned every row in one response,
which doesn't survive a bank with hundreds of thousands of items (SCALE_PLAN.md S2)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Query, Request, Response
from sqlalchemy import and_, or_

DEFAULT_LIMIT = 500
MAX_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class Page:
    limit: int
    offset: int


def page_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Rows per page (at most 1,000)"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
) -> Page:
    return Page(limit=limit, offset=offset)


def set_page_headers(request: Request, response: Response, page: Page, total: int, *, since: "Since | None" = None,
                     rows: list | None = None) -> None:
    response.headers["X-Total-Count"] = str(total)
    if since is not None:
        # A changed-since list pages by position, not offset: the next page starts after this
        # page's last row.
        if rows and len(rows) == page.limit:
            last = rows[-1]
            next_url = request.url.remove_query_params(["offset", "after_id", "updated_since"]).include_query_params(
                updated_since=last.updated_at.isoformat(), after_id=str(last.id), limit=page.limit)
            response.headers["Link"] = f'<{next_url}>; rel="next"'
        return
    if page.offset + page.limit < total:
        next_url = request.url.include_query_params(offset=page.offset + page.limit, limit=page.limit)
        response.headers["Link"] = f'<{next_url}>; rel="next"'


@dataclass
class Since:
    """A list's "changed since" position: rows changed at `time`, after the row `after_id`,
    and everything changed later."""

    time: datetime
    after_id: uuid.UUID | None = None


def updated_since_param(
    updated_since: datetime | None = Query(
        None, description="Only rows changed at or after this time (ISO 8601), in the order they changed. To page, pass "
                          "the last row's `updated_at` here and its `id` as `after_id`; never skip rows with `offset` while "
                          "they may be changing."),
    after_id: uuid.UUID | None = Query(None, description="With `updated_since`: start after this row (the last one you received)"),
) -> Since | None:
    if updated_since is None:
        return None
    if updated_since.tzinfo is None:
        updated_since = updated_since.replace(tzinfo=timezone.utc)
    return Since(updated_since, after_id)


def changed_since(model, since: Since | None, default_order: tuple) -> tuple[dict, tuple]:
    """For a list endpoint: (extra repository filters, order). With `since`, only rows changed
    at or after it (or, with after_id, strictly after that row in (updated_at, id) order), the
    oldest change first, so a data pipeline fetches only what changed and can page without
    missing a row that changes meanwhile (docs/guides/bi.md); otherwise the endpoint's usual
    order."""
    if since is None:
        return {}, default_order
    if since.after_id is None:
        condition = model.updated_at >= since.time
    else:
        condition = or_(model.updated_at > since.time, and_(model.updated_at == since.time, model.id > since.after_id))
    return {"where": [condition]}, (model.updated_at, model.id)
