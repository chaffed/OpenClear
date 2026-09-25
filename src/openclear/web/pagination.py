# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

ItemT = TypeVar("ItemT")

DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class Page(Generic[ItemT]):
    """One page of a list route's results, plus everything
    `templates/_macros/pagination.html`'s pager needs to render "Showing X-Y of Z" and
    Prev/Next links without the template doing any arithmetic itself."""

    items: list[ItemT]
    page: int
    page_size: int
    total_count: int

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(self.total_count / self.page_size))

    @property
    def start_index(self) -> int:
        return 0 if self.total_count == 0 else (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        return min(self.page * self.page_size, self.total_count)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def paginate(
    *,
    page: int,
    count_fn: Callable[[], int],
    list_fn: Callable[..., list[ItemT]],
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Page[ItemT]:
    """The one place every paginated list route computes its offset/limit and total
    count — used identically by every list router (issued_items, paid_items,
    ach_transactions, ach_authorizations, exceptions, stop_payments, accounts,
    customers, users, security_groups, ach_return_reasons, check_images, audit_log) so
    the arithmetic lives in exactly one place, regardless of whether that router talks
    to a repository directly or goes through a service function.

    `count_fn` takes no arguments and returns the total row count for the route's
    current filters (typically `SomeRepository(...).count(**filters)`). `list_fn` is
    called as `list_fn(limit=..., offset=...)` and must return that page's rows
    (typically the same repo's `.list(**filters, limit=..., offset=..., order_by=...)`,
    or a service function that forwards limit/offset/order_by to one).

    `page` is 1-indexed, clamped to at least 1, and also clamped to the last real page
    once `total_count` is known — requesting page 99 of a 2-page result quietly shows
    page 2 instead of an empty page with a nonsensical "showing 4901-60 of 60" — same
    "never 500 (or show something nonsensical) on a query param a user could easily
    hand-type" spirit as the rest of this app's web routes."""
    total_count = count_fn()
    total_pages = max(1, math.ceil(total_count / page_size))
    page = min(max(1, page), total_pages)
    items = list_fn(limit=page_size, offset=(page - 1) * page_size)
    return Page(items=items, page=page, page_size=page_size, total_count=total_count)
