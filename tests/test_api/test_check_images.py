# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from tests.conftest import login_headers
from tests.test_ocr.conftest import make_check_image

# Check-image storage isolation is now handled globally by conftest.py's
# isolated_filesystem_settings autouse fixture.


def test_upload_check_image_runs_ocr_and_populates_fields(client, engine, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ocr-upload")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    image_bytes = make_check_image(payee="Acme Test Vendor", amount="250.00")
    resp = client.post(
        "/api/v1/check-images",
        headers=headers,
        files={"front_image": ("check.png", image_bytes, "image/png")},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["ocr_status"] == "pending"  # response reflects pre-background-task state

    # Once the queued OCR job has run (finished here, so a slow machine can't race it), a
    # fresh fetch shows the final OCR result.
    from openclear.workers import jobs

    jobs.drain(engine)
    got = client.get(f"/api/v1/check-images/{resp.json()['id']}", headers=headers)
    assert got.status_code == 200, got.text
    fetched = got.json()
    assert fetched["ocr_status"] == "completed"
    assert fetched["ocr_extracted_payee"] == "Acme Test Vendor"
    assert fetched["ocr_extracted_amount"] == "250.00"
    assert fetched["ocr_provider"] == "tesseract"


def test_check_image_with_payee_mismatch_escalates_already_matched_paid_item(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ocr-escalate")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    client.post(
        "/api/v1/issued-items",
        headers=headers,
        json={
            "account_id": str(account.id),
            "check_number": "1234",
            "amount": "250.00",
            "payee_name": "Correct Payee LLC",
            "issue_date": "2026-01-01",
        },
    )
    paid = client.post(
        "/api/v1/paid-items",
        headers=headers,
        json={
            "account_id": str(account.id),
            "check_number": "1234",
            "presented_amount": "250.00",
            "presented_date": "2026-01-10",
        },
    ).json()
    assert paid["match_status"] == "matched"

    image_bytes = make_check_image(payee="Totally Different Fraudster", amount="250.00")
    upload = client.post(
        "/api/v1/check-images",
        headers=headers,
        data={"paid_item_id": paid["id"]},
        files={"front_image": ("check.png", image_bytes, "image/png")},
    )
    assert upload.status_code == 201, upload.text

    # The previously-clean match must now show up as an exception, discovered only once
    # OCR completed after the fact — this is the retroactive-escalation path.
    refreshed_paid = client.get(f"/api/v1/paid-items/{paid['id']}", headers=headers).json()
    assert refreshed_paid["match_status"] == "exception"

    exceptions = client.get("/api/v1/exceptions", headers=headers).json()
    assert len(exceptions) == 1
    assert "payee_mismatch" in exceptions[0]["exception_types"]


def test_reprocess_check_image(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ocr-reprocess")
    headers = login_headers(client, tenant.slug, users["preparer"].email)

    image_bytes = make_check_image(payee="Reprocess Test Co", amount="99.99")
    uploaded = client.post(
        "/api/v1/check-images",
        headers=headers,
        files={"front_image": ("check.png", image_bytes, "image/png")},
    ).json()

    reprocessed = client.post(f"/api/v1/check-images/{uploaded['id']}/reprocess", headers=headers)
    assert reprocessed.status_code == 200

    fetched = client.get(f"/api/v1/check-images/{uploaded['id']}", headers=headers).json()
    assert fetched["ocr_status"] == "completed"
    assert fetched["ocr_extracted_payee"] == "Reprocess Test Co"


def test_check_images_are_tenant_isolated(client, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="ocr-isolation-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="ocr-isolation-b")
    headers_a = login_headers(client, tenant_a.slug, users_a["preparer"].email)
    headers_b = login_headers(client, tenant_b.slug, users_b["preparer"].email)

    uploaded = client.post(
        "/api/v1/check-images",
        headers=headers_a,
        files={"front_image": ("check.png", make_check_image(), "image/png")},
    ).json()

    resp = client.get(f"/api/v1/check-images/{uploaded['id']}", headers=headers_b)
    assert resp.status_code == 404
