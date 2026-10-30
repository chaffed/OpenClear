# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Access reviews and the checks that need new data (CONTROLS_PLAN.md CT3)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.access_review import AccessReview, AccessReviewItem
from openclear.domain.api_client import ApiClient
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bulk_upload_file import BulkUploadCorrection, BulkUploadFile
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import api_client_service, control_checks
from tests.test_web.test_customer_people import _login, _setup
from tests.test_web.test_web_bulk_ach import _nacha_batch_header, _nacha_entry_detail


def _items(db_session, review_id):
    import uuid

    review_id = uuid.UUID(str(review_id))
    db_session.expire_all()
    return {i.who: i for i in db_session.execute(select(AccessReviewItem).where(AccessReviewItem.review_id == review_id)).scalars()}


def _start(client, csrf):
    started = client.post("/ui/access-reviews", data={"csrf_token": csrf}, follow_redirects=False)
    assert started.status_code == 303, started.text
    return started.headers["location"].rsplit("/", 1)[1]


def _decide(client, csrf, review_id, item, decision, note=""):
    return client.post(f"/ui/access-reviews/{review_id}/items/{item.id}", data={"csrf_token": csrf, "decision": decision, "note": note},
                       follow_redirects=False)


def test_the_banks_review_of_every_membership(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ar-bank")
    tenant, users = s["tenant"], s["users"]
    api_client_service.create_client(db_session, tenant.id, name="Core sync", security_group_id=s["clerk"].id)
    db_session.commit()
    csrf = _login(client, tenant, users["admin"].email)
    review_id = _start(client, csrf)
    items = _items(db_session, review_id)
    assert users["viewer"].email in items and s["admin"].email in items and "API client: Core sync" in items  # staff, customers, clients
    assert items[s["admin"].email].scope == "Acme" and items[users["viewer"].email].scope == "Bank-wide"
    assert "already open" in client.post("/ui/access-reviews", data={"csrf_token": csrf}, follow_redirects=False).headers["location"].replace("+", " ")

    assert "Say+what+changed" in _decide(client, csrf, review_id, items[users["preparer"].email], "changed").headers["location"]
    _decide(client, csrf, review_id, items[users["viewer"].email], "removed")
    _decide(client, csrf, review_id, items["API client: Core sync"], "removed")
    _decide(client, csrf, review_id, items[users["preparer"].email], "changed", "Moved to Viewer")
    db_session.expire_all()
    viewer = db_session.execute(select(TenantMembership).where(TenantMembership.user_id == users["viewer"].id)).scalar_one()
    assert not viewer.is_active
    assert db_session.execute(select(ApiClient).where(ApiClient.name == "Core sync")).scalar_one().revoked_at is not None
    assert control_checks.access_review(db_session, tenant.id).status == "WARN"  # still open

    for item in _items(db_session, review_id).values():
        if item.decision is None:
            _decide(client, csrf, review_id, item, "confirmed")
    db_session.expire_all()
    import uuid

    review = db_session.get(AccessReview, uuid.UUID(review_id))
    assert review.completed_at is not None and len(review.decisions_sha256) == 64
    completed = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "access_review.completed")).scalar_one()
    assert completed.metadata_json["decisions_sha256"] == review.decisions_sha256
    assert control_checks.access_review(db_session, tenant.id).status == "PASS"
    page = client.get(f"/ui/access-reviews/{review_id}").text
    assert "0 to decide" in page and "Moved to Viewer" in page


def test_a_customer_reviews_only_its_own_people(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="ar-cust")
    tenant = s["tenant"]
    assert control_checks.customer_access_review(db_session, tenant.id).status == "WARN"  # Acme manages its people, no review yet
    csrf = _login(client, tenant, s["admin"].email)
    review_id = _start(client, csrf)
    items = _items(db_session, review_id)
    assert set(items) == {s["admin"].email}  # nobody else's, no bank staff
    refused = _decide(client, csrf, review_id, items[s["admin"].email], "removed")  # their only administrator
    assert refused.status_code == 303 and "administrator" in refused.headers["location"].replace("+", " ")
    _decide(client, csrf, review_id, items[s["admin"].email], "confirmed")
    assert control_checks.customer_access_review(db_session, tenant.id).status == "PASS"

    # Each side sees only its own reviews.
    bank_csrf = _login(client, tenant, s["users"]["admin"].email)
    assert client.get(f"/ui/access-reviews/{review_id}").status_code == 404
    _login(client, tenant, s["users"]["viewer"].email)
    assert client.get("/ui/access-reviews").status_code == 403
    assert bank_csrf


def test_files_reconciled_against_their_control_totals(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ar-file")
    entries = [_nacha_entry_detail(account.account_number, "0000015000", trace_number="123456780000001"),
               _nacha_entry_detail("NO-SUCH-ACCT", "0000020000", trace_number="123456780000002")]
    control = "8" + "200" + "000002" + "0024691356" + "0" * 12 + "000000035000" + " " * 50
    content = "\n".join([_nacha_batch_header(), *entries, control]).encode()
    from tests.test_web.test_web_bulk_ach import _login as ach_login

    csrf = ach_login(client, tenant.slug, users["admin"].email)
    client.post("/ui/ach/transactions/bulk", data={"csrf_token": csrf, "format": "nacha"},
                files={"upload_file": ("ach.txt", content, "text/plain")})
    db_session.expire_all()
    upload = db_session.execute(select(BulkUploadFile).where(BulkUploadFile.tenant_id == tenant.id)).scalar_one()
    assert (upload.control_item_count, str(upload.control_total), upload.loaded_item_count, str(upload.loaded_total)) == (2, "350.00", 1, "150.00")
    assert control_checks.file_reconciliation(db_session, tenant.id).status == "PASS"  # less than a day old
    upload.uploaded_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()
    result = control_checks.file_reconciliation(db_session, tenant.id)
    assert result.status == "WARN" and result.evidence["files_not_reconciled"][0]["items_missing"] == 1
    db_session.add(BulkUploadCorrection(tenant_id=tenant.id, bulk_upload_file_id=upload.id, row_index=1, data={}, success=True))
    db_session.commit()
    assert control_checks.file_reconciliation(db_session, tenant.id).status == "PASS"


def test_decision_delivery(db_session, tenant_factory):
    from openclear.domain.decision_outbox import DecisionBatch, DecisionOutbox

    tenant, _account, _users = tenant_factory.make(slug="ar-outbox")
    assert control_checks.decision_delivery(db_session, tenant.id).status == "PASS"  # files off
    db_session.add(DecisionOutbox(tenant_id=tenant.id, enabled=True))
    batch = DecisionBatch(tenant_id=tenant.id, layout="openclear_csv", from_sequence=1, to_sequence=2, decision_count=2, filename="d.csv",
                          media_type="text/csv", storage_ref="x", sha256="0" * 64, size=1,
                          created_at=datetime.now(timezone.utc) - timedelta(hours=30))
    db_session.add(batch)
    db_session.commit()
    assert control_checks.decision_delivery(db_session, tenant.id).status == "WARN"
    batch.acknowledged_at = datetime.now(timezone.utc)
    db_session.commit()
    assert control_checks.decision_delivery(db_session, tenant.id).status == "PASS"


def test_outside_services_and_standards(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="ar-std")
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    assert control_checks.outside_services(db_session, tenant.id).status == "WARN"
    assert control_checks.password_policy(db_session, tenant.id).status == "WARN"
    assert control_checks.key_age(db_session, tenant.id).status == "WARN"

    from tests.test_web.test_web_bulk_ach import _login as web_login

    csrf = web_login(client, tenant.slug, users["admin"].email)
    assert "Email delivery" in client.get("/ui/settings/outside-services").text
    client.post("/ui/settings/outside-services", data={"csrf_token": csrf, "service_key": "email", "note": "SOC 2 report read"})
    client.post("/ui/settings/standards", data={"csrf_token": csrf, "standard_password_min_length": "20", "key_rotation_days": "3650"})
    db_session.expire_all()
    assert control_checks.outside_services(db_session, tenant.id).status == "PASS"
    assert control_checks.password_policy(db_session, tenant.id).status == "FAIL"  # 15 in force, standard 20
    tenant.password_min_length = 20
    db_session.commit()
    assert control_checks.password_policy(db_session, tenant.id).status == "PASS"
    assert control_checks.key_age(db_session, tenant.id).status == "PASS"
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "tenant.outside_service_reviewed")).scalar_one()


def test_reviewing_a_service_that_isnt_used_says_so_plainly(client, db_session, tenant_factory):
    """plans/HOUSEKEEPING_PLAN.md HK1: a fixed sentence, whatever the error inside."""
    tenant, _account, users = tenant_factory.make(slug="outside-unknown")
    _login(client, tenant, users["admin"].email)
    answer = client.post("/ui/settings/outside-services", data={"csrf_token": client.cookies.get("csrf_token"), "service_key": "nope"},
                         headers={"x-requested-with": "fetch"})
    assert answer.json() == {"saved": True, "message": "That isn't a service OpenClear uses for you.", "key": "outside-services"}
