# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The `openclear` command (sdks/python, SDK_PLAN.md P3) against the real app."""

import csv
import io
import json
import threading
import time

import httpx

from openclear.domain.decision import DecisionOutcome
from openclear.services import api_client_service, decision_outbox_service, security_group_service, webhook_service
from tests.test_ml.test_per_customer_ml import _decide, _make_exception

from openclear_sdk import cli  # noqa: E402 -- from sdks/python/src (conftest.py)


def _creds(db_session, tenant, monkeypatch):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="CLI", security_group_id=group.id)
    db_session.commit()
    monkeypatch.setenv("OPENCLEAR_URL", "http://testserver")
    monkeypatch.setenv("OPENCLEAR_CLIENT_ID", issued.client.client_id)
    monkeypatch.setenv("OPENCLEAR_CLIENT_SECRET", issued.secret)


def _run(client, *argv):
    out = io.StringIO()
    code = cli.main(list(argv), http_client=client, out=out)
    return code, out.getvalue()


def test_push_reports_rows(client, db_session, tenant_factory, monkeypatch, tmp_path):
    tenant, account, _users = tenant_factory.make(slug="cli-push")
    _creds(db_session, tenant, monkeypatch)
    good = tmp_path / "presented.csv"
    good.write_text(f"account_number,check_number,presented_amount,presented_date\n{account.account_number},C1,10.00,2026-03-02\nNOPE,C2,1.00,2026-03-02\n")
    code, out = _run(client, "push", "paid_items", str(good))
    assert code == 1 and "1 imported, 1 failed" in out and "row 3:" in out  # one bad row: exit 1, and which one

    monkeypatch.setenv("OPENCLEAR_CLIENT_SECRET", "wrong")
    assert _run(client, "push", "paid_items", str(good))[0] == 2


def test_decisions_pull_and_files(client, db_session, tenant_factory, monkeypatch, tmp_path):
    tenant, account, users = tenant_factory.make(slug="cli-decisions")
    _creds(db_session, tenant, monkeypatch)
    for n in range(2):
        _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, f"D{n}", "1.00", "2.00"), DecisionOutcome.RETURN)

    code, out = _run(client, "decisions", "pull", "--consumer", "core", "--format", "csv")
    rows = list(csv.DictReader(io.StringIO(out)))
    assert code == 0 and [r["sequence"] for r in rows] == ["1", "2"] and rows[0]["outcome"] == "return"
    assert _run(client, "decisions", "pull", "--consumer", "core")[1] == ""  # acknowledged: nothing new
    lines = _run(client, "decisions", "pull", "--consumer", "audit", "--no-ack")[1].splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == [1, 2]

    decision_outbox_service.run(db_session, tenant.id)
    db_session.commit()
    code, out = _run(client, "decisions", "files", "--download", str(tmp_path / "outbox"))
    files = list((tmp_path / "outbox").iterdir())
    assert code == 0 and len(files) == 1 and files[0].read_text().startswith("sequence,")
    assert _run(client, "decisions", "files", "--download", str(tmp_path / "outbox2"))[1] == ""  # acknowledged


def test_webhooks_tail_verifies_and_prints():
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    out = io.StringIO()
    server = cli.tail_server(secret, 0, out)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    body = json.dumps({"type": "exception.created", "timestamp": "t", "data": {"exception_id": "x"}}).encode()
    now = int(time.time())
    signed = {"webhook-id": "msg_1", "webhook-timestamp": str(now), "webhook-signature": webhook_service.sign(secret, "msg_1", now, body)}
    assert httpx.post(url, content=body, headers=signed).status_code == 204
    assert httpx.post(url, content=body + b" ", headers=signed).status_code == 400
    server.shutdown()
    [line] = out.getvalue().splitlines()
    assert json.loads(line)["type"] == "exception.created" and json.loads(line)["id"] == "msg_1"
