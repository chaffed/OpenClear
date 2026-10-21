# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What an operator watches (FULL_REVIEW_PLAN.md FR9): the metrics endpoint, and SQLite set up
for two writers."""

from sqlalchemy import text

from openclear.config import Settings
from openclear.db.session import create_db_engine


def test_a_file_sqlite_database_uses_wal_and_waits_for_a_lock(tmp_path):
    engine = create_db_engine(Settings(database_url=f"sqlite:///{tmp_path / 'one.db'}", environment="development"))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    engine.dispose()


def test_metrics_need_an_operations_key_and_say_what_to_watch(client, db_session, tenant_factory):
    from openclear.services import platform_api_key_service

    assert client.get("/api/v1/platform/metrics").status_code in (401, 403)
    key, key_hash = platform_api_key_service.mint()
    platform_api_key_service.create_from_hash(db_session, "monitoring", ("operations",), key_hash)
    db_session.commit()
    resp = client.get("/api/v1/platform/metrics", headers={"X-Api-Key": key})
    assert resp.status_code == 200
    for name in ("openclear_jobs", "openclear_decisions_unsent_past_cutoff", "openclear_decision_files_unacknowledged",
                 "openclear_http_requests_total"):
        assert name in resp.text
