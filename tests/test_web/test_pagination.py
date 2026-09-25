# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.web.pagination import paginate


def _paginate(total: int, *, page: int, page_size: int = 10):
    rows = list(range(total))

    def count_fn():
        return total

    def list_fn(*, limit, offset):
        return rows[offset : offset + limit]

    return paginate(page=page, count_fn=count_fn, list_fn=list_fn, page_size=page_size)


def test_first_page():
    page_obj = _paginate(25, page=1)
    assert page_obj.items == list(range(10))
    assert page_obj.page == 1
    assert page_obj.total_pages == 3
    assert page_obj.start_index == 1
    assert page_obj.end_index == 10
    assert page_obj.has_prev is False
    assert page_obj.has_next is True


def test_middle_page():
    page_obj = _paginate(25, page=2)
    assert page_obj.items == list(range(10, 20))
    assert page_obj.start_index == 11
    assert page_obj.end_index == 20
    assert page_obj.has_prev is True
    assert page_obj.has_next is True


def test_last_page_partial():
    page_obj = _paginate(25, page=3)
    assert page_obj.items == list(range(20, 25))
    assert page_obj.start_index == 21
    assert page_obj.end_index == 25
    assert page_obj.has_next is False


def test_page_below_one_clamps_to_one():
    page_obj = _paginate(25, page=0)
    assert page_obj.page == 1
    assert page_obj.items == list(range(10))


def test_page_past_the_end_clamps_to_last_real_page():
    page_obj = _paginate(25, page=99)
    assert page_obj.page == 3
    assert page_obj.items == list(range(20, 25))
    assert page_obj.start_index == 21
    assert page_obj.end_index == 25


def test_empty_result_set():
    page_obj = _paginate(0, page=1)
    assert page_obj.items == []
    assert page_obj.total_pages == 1
    assert page_obj.start_index == 0
    assert page_obj.end_index == 0
    assert page_obj.has_prev is False
    assert page_obj.has_next is False
