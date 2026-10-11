# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The TypeScript SDK (sdks/typescript, SDK_PLAN.md P1) against a real running OpenClear:
this starts the app on a local port, sets up a bank, API clients, decisions and a signed
webhook, and runs the SDK's own tests (node --test) against it. Skipped without Node 22.6+,
which runs TypeScript directly."""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from openclear.config import get_settings
from openclear.domain.decision import DecisionOutcome
from openclear.services import api_client_service, security_group_service, webhook_service
from tests.test_ml.test_per_customer_ml import _decide, _make_exception

SDK = Path(__file__).resolve().parents[2] / "sdks" / "typescript"


def _node() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor, *_ = (int(p) for p in version.split("."))
    return node if (major, minor) >= (23, 6) or (major == 22 and minor >= 18) else None


NODE = _node()
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.18+ (runs TypeScript directly)")


@pytest.fixture
def engine(engine, tmp_path):
    """A real server handles each request on its own thread, and the SDK's requests can
    overlap. The suite's in-memory SQLite is a single shared connection, which overlapping
    transactions break ("cannot commit - no transaction is active"). So here it's a SQLite
    file with an ordinary connection pool, as in production: each thread its own connection.
    Postgres runs already pool connections."""
    if engine.dialect.name != "sqlite":
        yield engine
        return
    from sqlalchemy import create_engine, event

    from openclear.db.base import Base
    from tests.conftest import _seed_payment_networks

    pooled = create_engine(f"sqlite:///{tmp_path / 'sdk.db'}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(pooled, "connect")
    def _fk_pragma(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(pooled)
    _seed_payment_networks(pooled)
    yield pooled
    pooled.dispose()


@pytest.fixture
def server(app):
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        assert time.monotonic() < deadline, "server didn't start"
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


def _api_client(db_session, tenant, group, *, can_act=False):
    issued = api_client_service.create_client(
        db_session, tenant.id, name=f"TS {group}", can_act_for_users=can_act,
        security_group_id=security_group_service.get_security_group_by_name(db_session, tenant.id, group).id,
    )
    db_session.commit()
    return issued.client.client_id, issued.secret


def test_the_typescript_sdk_against_a_running_server(server, db_session, tenant_factory, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="sdk-ts")
    monkeypatch.setattr(get_settings(), "bulk_api_sync_max_rows", 2)
    for n in range(3):
        exception = _make_exception(db_session, tenant, account, users, f"TSD{n}", "1.00", "2.00")
        _decide(db_session, tenant, users, exception, DecisionOutcome.PAY)
    client_id, secret = _api_client(db_session, tenant, "Admin", can_act=True)
    viewer_id, viewer_secret = _api_client(db_session, tenant, "Viewer")

    hook_secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    body = json.dumps({"type": "exception.created", "timestamp": "2026-10-08T00:00:00Z", "data": {"exception_id": "x"}})
    now = int(time.time())
    headers = {"webhook-id": "msg_ts", "webhook-timestamp": str(now),
               "webhook-signature": webhook_service.sign(hook_secret, "msg_ts", now, body.encode())}

    env = {
        **os.environ,
        "OPENCLEAR_TEST_URL": server,
        "OPENCLEAR_TEST_CLIENT_ID": client_id, "OPENCLEAR_TEST_CLIENT_SECRET": secret,
        "OPENCLEAR_TEST_VIEWER_ID": viewer_id, "OPENCLEAR_TEST_VIEWER_SECRET": viewer_secret,
        "OPENCLEAR_TEST_ACCOUNT_ID": str(account.id), "OPENCLEAR_TEST_ACCOUNT_NUMBER": account.account_number,
        "OPENCLEAR_TEST_PERSON": users["preparer"].email,
        "OPENCLEAR_TEST_WEBHOOK_SECRET": hook_secret, "OPENCLEAR_TEST_WEBHOOK_HEADERS": json.dumps(headers),
        "OPENCLEAR_TEST_WEBHOOK_BODY": body,
    }
    result = subprocess.run(
        # The files themselves: older Node releases don't accept a directory here.
        [NODE, "--test", "--test-concurrency=1", *sorted(str(p.relative_to(SDK)) for p in (SDK / "test").glob("*.test.ts"))],
        cwd=SDK, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# skipped 0" in result.stdout or "ℹ skipped 0" in result.stdout, result.stdout[-3000:]
