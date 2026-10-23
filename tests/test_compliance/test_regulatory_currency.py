# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The regulatory pages' dates (REGULATORY_PLAN.md RG7): each page says when it was last checked
against its sources, the same date as compliance/regulatory.py, and the control check warns once a
page is more than a year old."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openclear.compliance import regulatory
from openclear.services import control_checks

DOCS = Path(__file__).resolve().parents[2] / "docs" / "compliance"


@pytest.mark.parametrize("page", regulatory.PAGES, ids=lambda p: p.path)
def test_each_page_says_the_date_in_the_registry(page):
    text = (DOCS / page.path).read_text()
    found = re.search(r"\*Current as of (\d{4}-\d{2}-\d{2})\.", text)
    assert found, f"{page.path} has no 'Current as of' line"
    assert found.group(1) == page.current_as_of.isoformat(), f"{page.path} says {found.group(1)}, the registry {page.current_as_of}"


def test_every_dated_page_is_in_the_registry():
    dated = {p.name for p in DOCS.glob("*.md") if "*Current as of " in p.read_text()}
    assert dated == {p.path for p in regulatory.PAGES}


def test_the_check_warns_after_a_year(monkeypatch, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="reg-currency")
    newest = max(p.current_as_of for p in regulatory.PAGES)
    oldest = min(p.current_as_of for p in regulatory.PAGES)
    at = lambda day: datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)  # noqa: E731
    monkeypatch.setattr(control_checks, "_now", lambda: at(newest + timedelta(days=1)))
    assert control_checks.regulatory_currency(db_session, tenant.id).status == "PASS"
    monkeypatch.setattr(control_checks, "_now", lambda: at(oldest + timedelta(days=regulatory.REVIEW_DAYS + 1)))
    result = control_checks.regulatory_currency(db_session, tenant.id)
    assert result.status == "WARN" and "in over a year" in result.detail
