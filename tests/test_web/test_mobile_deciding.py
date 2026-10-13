# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deciding on a phone and a tablet (MOBILE_PLAN.md R2): the exception queue as cards, its
filters in a bottom sheet. The browser tests use Playwright's Chromium and are skipped
where it isn't installed."""

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.exception_item import ExceptionItem
from tests.test_ml.test_per_customer_ml import _make_customer_with_account, _make_exception
from tests.test_web.test_deciding import _login
from tests.test_web.test_mobile_components import _sign_in, browser_page  # noqa: F401 -- a fixture


def test_the_queue_is_cards_with_checkboxes_on_a_phone(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="queue-cards")
    for number in ("8601", "8602"):
        _make_exception(db_session, tenant, account, users, number, "10.00", "99.00")
    _sign_in(client, tenant, users)

    page = browser_page(360)
    page.goto("http://testserver/ui/exceptions")
    card = page.locator("table.responsive tbody tr").first
    assert page.evaluate("getComputedStyle(document.querySelector('table.responsive tbody tr')).display") == "grid"
    box = card.locator("label.select-target").bounding_box()
    title, money = card.locator("td.cell-primary").bounding_box(), card.locator("td.cell-money").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert box["x"] + box["width"] <= title["x"] + 1 and money["x"] > title["x"]  # left of the title, clear of the amount

    # Ticking items shows the bulk bar, stuck to the bottom of the screen.
    bar = page.locator("[data-bulk-bar]")
    assert not bar.is_visible()
    card.locator("label.select-target").click()
    page.locator("table.responsive tbody tr").nth(1).locator("label.select-target").click()
    assert bar.is_visible() and "2 selected" in bar.inner_text()
    assert bar.bounding_box()["y"] + bar.bounding_box()["height"] <= 800 + 1

    # Return's reasons are a sheet, still part of the bulk form: returning both works.
    assert not page.locator("details.bulk-return > summary").is_visible()
    page.locator("[data-sheet-open='bulk-return']").click()
    sheet = page.get_by_role("dialog", name="Return the selected items")
    sheet.locator("select[name='check_return_reason_id']").select_option(label="Stop payment")
    sheet.get_by_role("button", name="Return selected").click()
    # (The test's server doesn't follow the redirect into the browser, so wait for the answer.)
    with page.expect_response("**/ui/exceptions/bulk") as answer:
        page.locator("dialog.confirm-dialog").get_by_role("button", name="Return 2").click()
    assert "flash=Returned%202%20items" in answer.value.headers["location"]
    db_session.expire_all()
    assert sorted(i.status.value for i in db_session.query(ExceptionItem).filter_by(tenant_id=tenant.id)) == ["return", "return"]

    # Network and status are in a sheet; the button says what's chosen.
    page = browser_page(360)
    page.goto("http://testserver/ui/exceptions?status=all")
    trigger = page.locator("[data-sheet-open='queue-filters']")
    assert trigger.inner_text().strip() == "All statuses · Checks and ACH"
    assert not page.locator("select[name='network_code']").is_visible()
    trigger.click()
    sheet = page.get_by_role("dialog", name="Network and status")
    sheet.locator("select[name='network_code']").select_option("check")
    page.wait_for_url("**network_code=check**")
    assert page.locator("[data-sheet-open='queue-filters']").inner_text().strip() == "All statuses · Checks"


def test_the_queue_on_a_desktop_is_a_table_with_row_keys(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="queue-desk")
    for number in ("8701", "8702"):
        _make_exception(db_session, tenant, account, users, number, "10.00", "99.00")
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto("http://testserver/ui/exceptions")
    assert page.locator("select[name='status']").is_visible()
    assert not page.locator("[data-sheet-open='queue-filters']").is_visible()
    assert page.locator("table.responsive thead th").count() == 7
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.locator("tr.row-focused").count() == 1
    assert page.evaluate("document.activeElement.textContent").startswith("Check #87")
    page.locator("thead [data-select-all]").check()
    assert page.locator("[data-bulk-bar]").is_visible() and "2 selected" in page.locator("[data-bulk-count]").inner_text()


def test_the_decision_bar_on_a_phone(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="decision-bar")
    item = _make_exception(db_session, tenant, account, users, "8901", "10.00", "99.00")
    item.ml_score = 0.2  # an 80% chance it should be returned: High
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    assert not page.locator(".decision-score").is_visible()
    assert page.locator(".decision-return > summary").is_visible()
    assert not page.locator("[data-sheet-open='return-decide']").is_visible()

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    bar = page.locator(".decision-card").bounding_box()
    assert bar["y"] + bar["height"] <= 780 + 1 and bar["height"] < 160  # stuck to the bottom, and small
    score = page.locator(".decision-score")
    pay = page.get_by_role("button", name="Pay", exact=True)
    ret = page.locator("[data-sheet-open='return-decide']")
    assert "High · 80%" in score.inner_text()
    boxes = [score.bounding_box(), pay.bounding_box(), ret.bounding_box()]
    assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"] and abs(boxes[1]["y"] - boxes[2]["y"]) < 2  # one row
    assert all(b["height"] >= 44 for b in boxes[1:])

    # R opens the reasons in a sheet, the first one focused; returning from it works.
    page.keyboard.press("r")
    sheet = page.get_by_role("dialog", name="Why is it being returned?")
    assert sheet.is_visible()
    assert page.evaluate("document.activeElement.name") == "return_reason_id"
    sheet.get_by_label("Stop payment").check()
    with page.expect_response(f"**/ui/exceptions/{item.id}/decide") as answer:
        sheet.get_by_role("button", name="Return it").click()
    assert "Decision+recorded" in answer.value.headers["location"]
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "return"


def test_pay_on_a_phone_asks_first_when_the_risk_is_high(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="decision-confirm")
    item = _make_exception(db_session, tenant, account, users, "8902", "10.00", "99.00")
    item.ml_score = 0.2
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    page.get_by_role("button", name="Pay", exact=True).click()
    confirm = page.locator("dialog.confirm-dialog")
    assert confirm.is_visible()
    box = confirm.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    for name in ("Cancel", "Pay it"):
        assert confirm.get_by_role("button", name=name).bounding_box()["height"] >= 44
    confirm.get_by_role("button", name="Cancel").click()
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "open"


def _png(colour: str) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (600, 260), colour).save(out, format="PNG")
    return out.getvalue()


def test_the_item_on_a_phone_image_first_one_side_at_a_time(client, db_session, tenant_factory, browser_page):  # noqa: F811
    from openclear.networks.check.ocr_processing import create_check_image

    tenant, account, users = tenant_factory.make(slug="item-phone")
    item = _make_exception(db_session, tenant, account, users, "9001", "10.00", "99.00")
    create_check_image(db_session, tenant.id, front_bytes=_png("white"), back_bytes=_png("grey"), paid_item_id=item.source_item_id)
    db_session.commit()
    _sign_in(client, tenant, users)

    page = browser_page(1280)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    front, back = page.get_by_role("img", name="Front of the check"), page.get_by_role("img", name="Back of the check")
    assert front.is_visible() and back.is_visible() and page.locator(".image-sides").count() == 0

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    front, back = page.get_by_role("img", name="Front of the check"), page.get_by_role("img", name="Back of the check")
    image_top = page.locator(".decide-image").bounding_box()["y"]
    assert image_top < page.locator("table.evidence").bounding_box()["y"]  # the check comes first
    sides = page.get_by_role("group", name="Side of the check")
    assert front.is_visible() and not back.is_visible()
    assert sides.get_by_role("button", name="Front").get_attribute("aria-pressed") == "true"
    sides.get_by_role("button", name="Back").click()
    assert back.is_visible() and not front.is_visible()
    assert sides.get_by_role("button", name="Back").bounding_box()["height"] >= 44
    # Wider again, both sides show and the buttons go.
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_function("document.querySelector('.image-sides') === null")
    assert front.is_visible() and back.is_visible()


def test_the_items_action_log_collapsed_with_the_newest_showing(client, db_session, tenant_factory, browser_page):  # noqa: F811
    from openclear.services import security_group_service, user_service
    from tests.conftest import TenantFactory

    tenant, account, users = tenant_factory.make(slug="item-log", require_dual_control=True)
    item = _make_exception(db_session, tenant, account, users, "9101", "10.00", "99.00")
    _sign_in(client, tenant, users)
    csrf = client.cookies.get("csrf_token")
    client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay", "notes": "first look"})
    client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay", "notes": "second look"})

    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}")
    log = page.locator("details.item-history")
    assert log.get_attribute("open") is None
    latest = log.locator(".history-latest")
    assert latest.is_visible() and "Recommended pay" in latest.inner_text() and users["admin"].email in latest.inner_text()
    log.locator("summary").click()
    assert log.locator(".history-list li").count() == 2 and not latest.is_visible()

    # Someone who can't read the action log, and a customer's own people, don't see it.
    _login(client, tenant, users["approver"].email)
    assert "item-history" not in client.get(f"/ui/exceptions/{item.id}").text
    customer, customer_account = _make_customer_with_account(db_session, tenant, "NG-1")
    theirs = _make_exception(db_session, tenant, customer_account, users, "9102", "10.00", "99.00")
    admin_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    clerk = user_service.create_user_with_membership(db_session, tenant.id, email="clerk@ng-1.example.com", password=TenantFactory.PASSWORD,
                                                     security_group_id=admin_group.id, customer_id=customer.id)
    db_session.commit()
    csrf = _login(client, tenant, users["admin"].email)
    client.post(f"/ui/exceptions/{theirs.id}/recommend", data={"csrf_token": csrf, "outcome": "pay"})
    assert "item-history" in client.get(f"/ui/exceptions/{theirs.id}").text
    _login(client, tenant, clerk.email)
    page = client.get(f"/ui/exceptions/{theirs.id}")
    assert page.status_code == 200 and "item-history" not in page.text


def test_undo_withdraws_ones_own_recommendation_only_before_approval(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="undo-rules", require_dual_control=True)
    item = _make_exception(db_session, tenant, account, users, "9201", "10.00", "99.00")
    csrf = _login(client, tenant, users["preparer"].email)

    first = client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay"}, follow_redirects=False)
    assert "undo=withdraw-recommendation" in first.headers["location"]
    page = client.get(first.headers["location"]).text
    assert 'data-undo-form="withdraw-recommendation"' in page and 'id="withdraw-recommendation" data-undo' in page
    # Revising it offers no Undo: that would withdraw the first one too.
    again = client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay", "notes": "sure"},
                        follow_redirects=False)
    assert "undo=" not in again.headers["location"]

    # Nobody else can withdraw it, not even someone who can approve it.
    csrf = _login(client, tenant, users["admin"].email)
    refused = client.post(f"/ui/exceptions/{item.id}/recommendation/withdraw", data={"csrf_token": csrf}, follow_redirects=False)
    assert "Only%20the%20person" in refused.headers["location"]
    assert "withdraw-recommendation" not in client.get(f"/ui/exceptions/{item.id}").text
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "pending_approval"

    csrf = _login(client, tenant, users["preparer"].email)
    done = client.post(f"/ui/exceptions/{item.id}/recommendation/withdraw", data={"csrf_token": csrf}, follow_redirects=False)
    assert "Recommendation+withdrawn" in done.headers["location"]
    db_session.expire_all()
    withdrawn = db_session.get(ExceptionItem, item.id)
    assert withdrawn.status.value == "open" and withdrawn.recommended_by_user_id is None and withdrawn.recommended_outcome is None
    entries = db_session.execute(select(AuditLogEntry.action).where(AuditLogEntry.resource_id == item.id)
                                 .order_by(AuditLogEntry.sequence)).scalars().all()
    assert entries[-1] == "exception.recommendation_withdrawn"

    # Once approved it's a decision, and a decision is never undone.
    client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay"})
    csrf = _login(client, tenant, users["approver"].email)
    client.post(f"/ui/exceptions/{item.id}/decide", data={"csrf_token": csrf, "outcome": "pay"})
    csrf = _login(client, tenant, users["preparer"].email)
    late = client.post(f"/ui/exceptions/{item.id}/recommendation/withdraw", data={"csrf_token": csrf}, follow_redirects=False)
    assert "no%20recommendation%20awaiting%20approval" in late.headers["location"]
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "pay"


def test_recommend_pay_with_one_tap_then_undo_on_a_phone(client, db_session, tenant_factory, browser_page):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="undo-phone", require_dual_control=True)
    item = _make_exception(db_session, tenant, account, users, "9301", "10.00", "99.00")
    csrf = _login(client, tenant, users["preparer"].email)
    # (The test's server doesn't follow redirects into the browser: open the page they lead to.)
    after = client.post(f"/ui/exceptions/{item.id}/recommend", data={"csrf_token": csrf, "outcome": "pay"}, follow_redirects=False)

    page = browser_page(360, 780)
    page.goto("http://testserver" + after.headers["location"])
    toast = page.locator(".toast")
    assert "Recommendation submitted." in toast.inner_text()
    undo = toast.get_by_role("button", name="Undo")
    assert undo.bounding_box()["height"] >= 44
    with page.expect_response(f"**/ui/exceptions/{item.id}/recommendation/withdraw") as answer:
        undo.click()
    assert "Recommendation+withdrawn" in answer.value.headers["location"]
    db_session.expire_all()
    assert db_session.get(ExceptionItem, item.id).status.value == "open"

    # The toast's Undo only ever submits a form the page marked data-undo.
    page = browser_page(360, 780)
    page.goto(f"http://testserver/ui/exceptions/{item.id}?flash=Hello&undo=return-recommend")
    assert page.locator("#return-recommend").count() == 1
    assert page.locator(".toast").is_visible() and page.locator(".toast-undo").count() == 0
