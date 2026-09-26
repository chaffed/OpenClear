# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Paging for the API's list endpoints.

Every list endpoint takes `limit` (default 500, at most 1,000) and `offset`, returns the
rows as a plain JSON list, oldest first, and adds two headers: `X-Total-Count` (rows
matching the filters) and, when there are more, a standard `Link: <...>; rel="next"`
pointing at the next page. Before 1.6 these endpoints returned every row in one response,
which doesn't survive a bank with hundreds of thousands of items (SCALE_PLAN.md S2)."""

from dataclasses import dataclass

from fastapi import Query, Request, Response

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


def set_page_headers(request: Request, response: Response, page: Page, total: int) -> None:
    response.headers["X-Total-Count"] = str(total)
    if page.offset + page.limit < total:
        next_url = request.url.include_query_params(offset=page.offset + page.limit, limit=page.limit)
        response.headers["Link"] = f'<{next_url}>; rel="next"'
