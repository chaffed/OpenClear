# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""main.py::_lifespan only starts the background scheduler when at least one of its
opt-in flags is set -- enable_disposition_scheduler was added to workers/scheduler.py
and config.py but never wired into this gate, so that scheduler could never actually
start even with the flag enabled. Caught while wiring the demo tenant startup hook into
the same function; this locks the fix in."""

from openclear.config import get_settings


def _reset_all_scheduler_flags(monkeypatch):
    settings = get_settings()
    for flag in ("enable_ml_scheduler", "auto_import_enabled", "notifications_enabled", "enable_disposition_scheduler", "demo_tenant_enabled"):
        monkeypatch.setattr(settings, flag, False)


def test_disposition_scheduler_flag_alone_starts_the_scheduler(monkeypatch):
    _reset_all_scheduler_flags(monkeypatch)
    monkeypatch.setattr(get_settings(), "enable_disposition_scheduler", True)

    started = []
    monkeypatch.setattr("openclear.workers.scheduler.start_scheduler", lambda: started.append(True))
    monkeypatch.setattr("openclear.workers.scheduler.stop_scheduler", lambda: None)

    from fastapi.testclient import TestClient
    from openclear.main import create_app

    with TestClient(create_app()):
        pass

    assert started == [True]


def test_no_scheduler_flags_never_starts_the_scheduler(monkeypatch):
    _reset_all_scheduler_flags(monkeypatch)

    started = []
    monkeypatch.setattr("openclear.workers.scheduler.start_scheduler", lambda: started.append(True))
    monkeypatch.setattr("openclear.workers.scheduler.stop_scheduler", lambda: None)

    from fastapi.testclient import TestClient
    from openclear.main import create_app

    with TestClient(create_app()):
        pass

    assert started == []
