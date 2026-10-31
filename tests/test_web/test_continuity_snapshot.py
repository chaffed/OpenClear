# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's open items as a signed file (CONTINUITY_PLAN.md BC-D5): written hourly, a dated copy
at the end of the bank's day, emailed to the incident contacts when asked, downloadable from
Settings → Continuity by the bank's staff and not by a customer's people."""

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select

from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant import Tenant
from openclear.services import continuity_snapshot, evidence_service, incident_service
from tests.test_decisions.test_changeable import _item
from tests.test_web.test_staff_and_operator import _login


def test_the_snapshot_lists_open_items_signed_and_keeps_a_daily_copy(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="snap")
    tenant.time_zone = "America/Chicago"
    _item(db_session, tenant, account, number="4101")
    _item(db_session, tenant, account, number="4102")
    incident_service.set_contacts(db_session, tenant.id, [incident_service.ContactInput("contact", "Ira", "ira@snap.example")], user_id=None)
    tenant.continuity_snapshot_emailed = True
    db_session.commit()

    midday = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)  # 12:00 in Chicago: the hourly file, no dated copy yet
    first = continuity_snapshot.write(db_session, tenant, midday)
    assert first.rows == 2 and first.dated_ref is None and first.emailed == []
    rows = list(csv.DictReader(io.StringIO(continuity_snapshot.read(tenant.id).decode())))
    assert [r["item"] for r in rows] == ["4101", "4102"] and rows[0]["network"] == "check" and rows[0]["decide_by"]
    described = continuity_snapshot.latest(tenant.id)
    assert evidence_service.verify(described) and described["bundle"]["rows"] == 2 and described["bundle"]["sha256"] == first.sha256

    evening = datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc)  # 23:30 CDT in Chicago on the 31st: the day's copy, emailed
    second = continuity_snapshot.write(db_session, tenant, evening)
    db_session.commit()
    assert second.dated_ref is not None and second.emailed == ["ira@snap.example"]
    assert continuity_snapshot.dated(tenant.id) == ["open-items-2026-10-31.csv"]
    [mail] = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.CONTINUITY_SNAPSHOT)).scalars().all()
    assert mail.destination == "ira@snap.example" and "4101" in mail.body and "decide by" in mail.body
    assert continuity_snapshot.write(db_session, tenant, evening).dated_ref is None  # once a day


def test_the_settings_section_and_the_download(client, db_session, tenant_factory):
    from openclear.services import security_group_service, user_service
    from tests.conftest import TenantFactory
    from tests.items import _make_customer_with_account

    tenant, account, users = tenant_factory.make(slug="snap-web")
    _item(db_session, tenant, account, number="4201")
    continuity_snapshot.write(db_session, tenant)
    db_session.commit()
    csrf = _login(client, tenant.slug, users["admin"].email)
    section = client.get("/ui/settings?from=continuity")
    assert section.status_code == 200
    got = client.get("/ui/settings/continuity/open-items.csv")
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/csv") and "4201" in got.text
    assert client.get("/ui/settings/continuity/open-items.csv?day=2020-01-01").status_code == 404
    saved = client.post("/ui/settings/continuity", data={"csrf_token": csrf, "emailed": "true"}, follow_redirects=False)
    assert saved.status_code in (200, 303)
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).continuity_snapshot_emailed

    customer, _acct = _make_customer_with_account(db_session, tenant, "SNAP-C")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="cust@snap-web.example.com", password=TenantFactory.PASSWORD,
                                                      security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    _login(client, tenant.slug, person.email)
    assert client.get("/ui/settings/continuity/open-items.csv").status_code == 404  # the bank's whole list isn't a customer's
