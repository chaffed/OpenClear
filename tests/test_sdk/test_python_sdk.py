# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The Python SDK (sdks/python, SDK_PLAN.md P1) against the real app: client-credentials
tokens, typed calls, paging, bulk with the 202 path, file uploads, the decision feed,
acting for a person, errors, and webhook verification. Retry behaviour is tested against a
mock transport, where failures can be staged."""

import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.decision import DecisionOutcome
from openclear.services import api_client_service, security_group_service, webhook_service
from tests.test_ml.test_per_customer_ml import _decide, _make_exception

import openclear_sdk  # noqa: E402 -- from sdks/python/src (conftest.py)
from openclear_sdk import (  # noqa: E402
    AuthenticationError,
    NotFoundError,
    OpenClear,
    PermissionDeniedError,
    ValidationError,
    WebhookVerificationError,
    webhooks,
)


def _sdk(client, db_session, tenant, *, can_act=False, group="Admin") -> OpenClear:
    security_group = security_group_service.get_security_group_by_name(db_session, tenant.id, group)
    issued = api_client_service.create_client(
        db_session, tenant.id, name="Core", security_group_id=security_group.id, can_act_for_users=can_act,
    )
    db_session.commit()
    sdk = OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client)
    sdk._sleep = lambda seconds: None
    sdk.bulk.poll_interval = 0
    sdk.api_client_id = issued.client.id
    return sdk


def _item(account, number, amount="10.00"):
    return {"account_id": str(account.id), "check_number": number, "amount": amount, "payee_name": "Vendor", "issue_date": "2026-03-01"}


def test_sync_create_and_page_through(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="sdk-basic")
    oc = _sdk(client, db_session, tenant)

    customer = oc.customers.upsert_customer("CIF-1", {"name": "Acme Corp"})
    synced = oc.accounts.upsert_account("SDK-ACCT-1", {"name": "Operating", "customer_number": "CIF-1"})
    assert synced["customer_id"] == customer["id"]
    for n in range(5):
        oc.issued_items.create_issued_item(_item(account, f"P{n}"))

    first_page = oc.issued_items.list_issued_items(limit=2)
    every = list(oc.issued_items.iter_issued_items(limit=2))
    assert len(first_page) == 2
    assert sorted(i["check_number"] for i in every) == [f"P{n}" for n in range(5)]


def test_errors_are_typed(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="sdk-errors")
    oc = _sdk(client, db_session, tenant)

    with pytest.raises(NotFoundError) as missing:
        oc.issued_items.get_issued_item(str(uuid.uuid4()))
    assert missing.value.status_code == 404
    with pytest.raises(ValidationError) as invalid:
        oc.issued_items.create_issued_item({**_item(account, "V1"), "issue_date": "not a date"})
    assert invalid.value.errors and "issue_date" in str(invalid.value)

    reader = _sdk(client, db_session, tenant, group="Viewer")
    with pytest.raises(PermissionDeniedError):
        reader.issued_items.create_issued_item(_item(account, "V2"))

    api_client_service.revoke(db_session, tenant.id, oc.api_client_id)
    db_session.commit()
    with pytest.raises(AuthenticationError):
        oc.issued_items.list_issued_items()


def test_bulk_submit_waits_for_background_batches_and_keeps_the_order(client, db_session, tenant_factory, monkeypatch):
    tenant, account, _users = tenant_factory.make(slug="sdk-bulk")
    monkeypatch.setattr(get_settings(), "bulk_api_sync_max_rows", 2)  # so a batch of 3 goes to the background
    oc = _sdk(client, db_session, tenant)
    items = [_item(account, f"B{n}") for n in range(5)]
    items[3]["account_id"] = str(uuid.uuid4())  # fails, alone

    result = oc.bulk.submit("issued_items", items, batch_size=3)
    assert (result.total, result.succeeded, result.failed) == (5, 4, 1)
    assert [r["index"] for r in result.results] == [0, 1, 2, 3, 4]
    assert result.failures[0]["index"] == 3 and len(result.upload_ids) == 1


def test_upload_a_file_and_read_its_rows(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="sdk-file")
    oc = _sdk(client, db_session, tenant)
    assert {"kind": "paid_items", "format": "tabular", "built_in": True} in oc.bulk_uploads.list_import_formats()

    csv = f"account_number,check_number,presented_amount,presented_date\n{account.account_number},F1,12.50,2026-03-02\n"
    upload = oc.bulk.upload_file("paid_items", "tabular", ("presented.csv", csv.encode(), "text/csv"))
    assert (upload["status"], upload["succeeded"]) == ("completed", 1)
    [row] = oc.bulk_uploads.iter_get_bulk_upload_results(upload["id"])
    assert row["success"]


def test_the_decision_feed_is_consumed_once_and_acknowledged(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="sdk-feed")
    for n in range(3):
        _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, f"D{n}", "1.00", "2.00"), DecisionOutcome.RETURN)
    oc = _sdk(client, db_session, tenant)

    handled = []
    assert oc.decision_feed.consume("core", handled.append, page_size=2) == 3
    assert [d["sequence"] for d in handled] == [1, 2, 3] and handled[0]["outcome"] == "return"
    assert oc.decision_feed.position("core") == 3
    assert oc.decision_feed.consume("core", handled.append) == 3 and len(handled) == 3  # nothing new

    csv = oc.decision_feed.decision_file(layout="openclear_csv")
    assert isinstance(csv, bytes) and csv.startswith(b"sequence,")

    def fail(_decision):
        raise RuntimeError("core down")

    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "D9", "1.00", "2.00"), DecisionOutcome.PAY)
    with pytest.raises(RuntimeError):
        oc.decision_feed.consume("core", fail)
    assert oc.decision_feed.position("core") == 3  # not acknowledged, so the next run gets it again


def test_acting_for_a_person_is_recorded_as_theirs(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="sdk-act")
    oc = _sdk(client, db_session, tenant, can_act=True)

    oc.acting_as(users["preparer"].email).issued_items.create_issued_item(_item(account, "A1"))
    entry = db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "issued_item.create")
    ).scalar_one()
    assert entry.actor_user_id == users["preparer"].id


def test_webhook_verification_matches_what_the_server_signs():
    secret = "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    body = json.dumps({"type": "exception.created", "timestamp": "2026-10-08T00:00:00Z", "data": {"exception_id": "x"}}).encode()
    now = int(time.time())
    headers = {"Webhook-Id": "msg_1", "Webhook-Timestamp": str(now), "Webhook-Signature": webhook_service.sign(secret, "msg_1", now, body)}

    assert webhooks.verify(secret, headers, body)["data"]["exception_id"] == "x"
    assert webhooks.sign(secret, "msg_1", now, body) == headers["Webhook-Signature"]
    with pytest.raises(WebhookVerificationError, match="signature"):
        webhooks.verify(secret, headers, body.replace(b'"x"', b'"y"'))
    with pytest.raises(WebhookVerificationError, match="too old"):
        webhooks.verify(secret, headers, body, now=now + 3600)
    with pytest.raises(WebhookVerificationError, match="Missing"):
        webhooks.verify(secret, {}, body)


# --- Retries, against staged failures ------------------------------------------------------


def _mock(responses):
    """An SDK whose requests get `responses` in turn (after the token request)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/oauth/token":
            return httpx.Response(200, json={"access_token": f"t{len(seen)}", "expires_in": 1800})
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    oc = OpenClear("https://oc.example", client_id="occ_x", client_secret="s", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    waits = []
    oc._sleep = waits.append
    return oc, seen, waits


def test_writes_are_retried_with_the_same_idempotency_key():
    oc, seen, waits = _mock([
        httpx.ConnectError("refused"),
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(201, json={"id": "1"}),
    ])
    assert oc.stop_payments.create_stop_payment({"account_id": "a", "check_number": "1"}) == {"id": "1"}
    writes = [r for r in seen if r.url.path != "/api/v1/oauth/token"]
    assert len(writes) == 4 and len({r.headers["Idempotency-Key"] for r in writes}) == 1
    assert waits[2] == 7.0  # Retry-After honoured


def test_retries_give_up_and_a_rejected_token_is_renewed_once():
    oc, _seen, _waits = _mock([httpx.Response(503)] * 4)
    oc.max_retries = 3
    with pytest.raises(openclear_sdk.ServerError):
        oc.issued_items.list_issued_items()

    oc, seen, _waits = _mock([httpx.Response(401, json={"detail": "expired"}), httpx.Response(200, json=[])])
    assert oc.issued_items.list_issued_items() == []
    tokens = [r for r in seen if r.url.path == "/api/v1/oauth/token"]
    assert len(tokens) == 2 and seen[-1].headers["Authorization"] == f"Bearer t{len(seen) - 1}"


def test_a_next_link_stays_on_the_configured_host():
    pages = [
        httpx.Response(200, json=[{"n": 1}], headers={"Link": '<http://evil.example/api/v1/issued-items?offset=1&limit=1>; rel="next"'}),
        httpx.Response(200, json=[{"n": 2}]),
    ]
    oc, seen, _waits = _mock(pages)
    assert [row["n"] for row in oc.issued_items.iter_issued_items(limit=1)] == [1, 2]
    assert seen[-1].url.host == "oc.example" and seen[-1].url.params["offset"] == "1"


def test_an_online_banking_platform_acts_for_its_signed_in_person(client, db_session, monkeypatch):
    """Token exchange and act-as-by-subject through the SDK (SDK_PLAN.md phase F)."""
    import openclear.auth.oidc_service as oidc_service
    from tests.conftest import TenantFactory
    from tests.test_api.test_obp_profile import _bank, _client, _id_token
    from tests.test_auth.oidc_helpers import FakeOidcProvider

    provider = FakeOidcProvider(issuer="https://mifos.example.com", client_id="openclear-at-mifos")
    monkeypatch.setattr(oidc_service, "_get_discovery_document", lambda issuer: provider.discovery_document())
    monkeypatch.setattr(oidc_service, "_get_jwks", lambda jwks_uri: provider.jwks())
    tenant, _users, connection, _acme, _beta, _a = _bank(db_session, TenantFactory(db_session), "sdk-obp", provider)
    issued = _client(db_session, tenant, connection)
    oc = OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client)

    jo = oc.for_person(_id_token(provider, sub="mifos-7"))
    assert jo.issued_items.list_issued_items() == []
    assert oc.acting_as_subject("mifos-7").issued_items.list_issued_items() == []
    with pytest.raises(AuthenticationError):
        oc.for_person("not-a-token")  # a refused exchange is a 400 invalid_grant
