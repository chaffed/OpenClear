# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields at a large bank's size (plans/CUSTOM_FIELDS_PLAN.md CF-D13): 25 fields on 20,000
accounts, half a million values. Times the account list, its filters, the API's list, an update
file of 1,000 rows and the data export. Seeding takes a while, so it runs only when asked:
OPENCLEAR_SCALE_TESTS=1 pytest -s tests/test_scale/ (the timings print with -s)."""

import os
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import insert

from openclear.domain.account import Account
from openclear.domain.custom_field import CustomFieldValue
from openclear.services import custom_field_service as cf, data_export_service
from openclear.services.custom_field_service import FieldInput
from tests.conftest import login_headers
from tests.test_auth.test_data_export_service import _run_job_sync
from tests.test_web.test_staff_and_operator import _login

pytestmark = pytest.mark.skipif(not os.environ.get("OPENCLEAR_SCALE_TESTS"), reason="set OPENCLEAR_SCALE_TESTS=1 to run")

ACCOUNTS = 20_000
FIELDS = 25
# Generous: these catch a page that reads every value, not a slow machine.
PAGE_SECONDS = 3.0
EXPORT_SECONDS = 120.0


def _timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.2f}s")
    return result, elapsed


def test_twenty_thousand_accounts_with_twenty_five_fields(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cf-scale")
    fields = [cf.create_field(db_session, tenant.id, "account", FieldInput(
        key=f"f{n:02d}", label=f"Field {n}", field_type="text", unique=n == 0, required=n == 1, show_in_list=n < 3), user_id=None)
        for n in range(FIELDS)]
    db_session.commit()
    now = datetime.now(timezone.utc)
    account_ids = [uuid.uuid4() for _ in range(ACCOUNTS)]

    def seed():
        db_session.execute(insert(Account), [
            {"id": i, "tenant_id": tenant.id, "account_number": f"9{n:07d}", "name": f"Account {n}", "created_at": now, "updated_at": now}
            for n, i in enumerate(account_ids)])
        for field in fields:
            rows = [{"id": uuid.uuid4(), "tenant_id": tenant.id, "field_id": field.id, "account_id": i, "text_value": f"V{n}",
                     "unique_text": f"V{n}" if field.unique else None, "updated_at": now}
                    for n, i in enumerate(account_ids) if not (field.required and n % 10 == 0)]  # a tenth lack the required one
            db_session.execute(insert(CustomFieldValue), rows)
        db_session.commit()

    _timed(f"seed {ACCOUNTS:,} accounts x {FIELDS} fields", seed)
    _login(client, tenant.slug, users["admin"].email)
    for label, url in (("account list", "/ui/accounts"), ("list filtered by a value", "/ui/accounts?field=f00&value=V12345"),
                       ("list missing a required field", "/ui/accounts?missing=1&page=50")):
        response, elapsed = _timed(label, lambda url=url: client.get(url))
        assert response.status_code == 200 and elapsed < PAGE_SECONDS, label
    assert "90012345" in client.get("/ui/accounts?field=f00&value=V12345").text

    headers = login_headers(client, tenant.slug, users["admin"].email)
    response, elapsed = _timed("API account list", lambda: client.get("/api/v1/accounts?limit=500", headers=headers))
    assert response.status_code == 200 and len(response.json()) == 500 and elapsed < PAGE_SECONDS

    body = "account_number,cf_f02\n" + "".join(f"9{n:07d},W{n}\n" for n in range(1000))
    response, elapsed = _timed("update file, 1,000 rows", lambda: client.post(
        "/ui/custom-fields/accounts/upload", data={"csrf_token": client.cookies.get("csrf_token")},
        files={"upload_file": ("fields.csv", body.encode(), "text/csv")}))
    assert "1000 of 1000 succeeded" in response.text and elapsed < EXPORT_SECONDS

    job = data_export_service.request_export(db_session, tenant.id, users["admin"].id, customer_id=None)
    db_session.commit()
    _result, elapsed = _timed("data export", lambda: _run_job_sync(db_session, job))
    assert elapsed < EXPORT_SECONDS
