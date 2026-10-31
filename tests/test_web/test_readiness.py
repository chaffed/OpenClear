# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""`/ready` and `openclear_component_up` (CONTINUITY_PLAN.md BC-D6): the database and storage
make the instance unavailable when they fail; Redis and the sweep only degrade it."""

from datetime import datetime, timedelta, timezone

from openclear import storage
from openclear.domain.scheduled_job_run import ScheduledJobRun
from openclear.services import continuity_service, readiness


def test_ready_names_each_component(client, db_session):
    answer = client.get("/ready")
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "ready" and body["components"]["database"]["ok"] and body["components"]["storage"]["ok"]
    assert body["components"]["scheduler"] == {"ok": True, "detail": "no run recorded yet"}
    assert "redis" not in body["components"]  # limits are in the process here


def test_storage_that_cant_be_written_makes_the_instance_unavailable(client, monkeypatch):
    def broken(area, key, data, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(storage, "put", broken)
    answer = client.get("/ready")
    assert answer.status_code == 503 and answer.headers["Retry-After"] == "30"
    assert answer.json()["status"] == "unavailable" and "can't write" in answer.json()["components"]["storage"]["detail"]


def test_a_stale_sweep_only_degrades(client, db_session):
    db_session.merge(ScheduledJobRun(job_id=continuity_service.SWEEP_JOB_ID, last_finished_at=datetime.now(timezone.utc) - timedelta(hours=2),
                                     last_ok=True))
    db_session.commit()
    answer = client.get("/ready")
    assert answer.status_code == 200 and answer.json()["status"] == "degraded"
    assert "minutes ago" in answer.json()["components"]["scheduler"]["detail"]
    found = readiness.components(db_session)
    assert readiness.status(found) == "degraded" and [c.name for c in found] == ["database", "storage", "scheduler"]


def test_the_metrics_carry_each_component(client, db_session, tenant_factory):
    from openclear import metrics

    text = metrics.render(db_session)
    assert 'openclear_component_up{component="database"} 1' in text and 'openclear_component_up{component="storage"} 1' in text
