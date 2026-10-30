# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import tenant_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _set_contact_info(db_session, tenant, **overrides):
    fields = {
        "support_email": None, "support_phone": None, "website": None,
        "address_line1": None, "address_line2": None, "city": None, "state": None, "postal_code": None,
    }
    fields.update(overrides)
    tenant_service.update_tenant_contact_info(db_session, tenant.id, **fields)
    db_session.commit()


def test_footer_shows_license_but_no_contact_line_when_none_configured(client, tenant_factory):
    # The footer itself always renders now (it carries the AGPL license notice on every
    # page), but the tenant-contact paragraph inside it stays conditional.
    tenant, _account, users = tenant_factory.make(slug="footer-absent")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    assert "app-footer" in resp.text
    footer_html = resp.text.split('class="app-footer"')[1].split("</footer>")[0]
    assert 'href="/ui/license"' in footer_html
    contact = footer_html.split('class="app-footer-license"')[0]
    assert "·" not in contact and "<p>" not in contact


def test_footer_present_post_login_when_contact_info_set(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="footer-post-login")
    _set_contact_info(db_session, tenant, support_email="help@footer-post-login.example.com")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    assert "app-footer" in resp.text
    assert "help@footer-post-login.example.com" in resp.text
    assert tenant.name in resp.text.split("app-footer")[1][:200]


def test_footer_present_on_branded_login_page(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="footer-branded-login")
    _set_contact_info(db_session, tenant, support_phone="(555) 444-5555")

    resp = client.get(f"/ui/login/{tenant.slug}")
    assert resp.status_code == 200
    assert "app-footer" in resp.text
    assert "(555) 444-5555" in resp.text


def test_footer_shows_license_but_no_contact_info_on_generic_unbranded_login_page(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="footer-generic-login")
    _set_contact_info(db_session, tenant, support_email="help@footer-generic-login.example.com")

    resp = client.get("/ui/login")
    assert "app-footer" in resp.text
    assert 'href="/ui/license"' in resp.text
    assert "help@footer-generic-login.example.com" not in resp.text


def test_footer_omits_unset_fields(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="footer-partial-fields")
    _set_contact_info(db_session, tenant, support_email="help@footer-partial-fields.example.com")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    footer_html = resp.text.split('class="app-footer"')[1].split("</footer>")[0]
    assert "help@footer-partial-fields.example.com" in footer_html
    # no address/phone/website were set -- none of their separators should appear stray
    assert footer_html.split('class="app-footer-license"')[0].count("·") == 1


def test_footer_contact_line_disappears_after_clearing_all_fields(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="footer-clear")
    _set_contact_info(db_session, tenant, support_email="help@footer-clear.example.com")
    _login(client, tenant.slug, users["admin"].email)
    assert "help@footer-clear.example.com" in client.get("/ui/").text

    _set_contact_info(db_session, tenant)  # all fields back to None
    resp = client.get("/ui/")
    # The footer itself (with its license notice) never disappears -- only the
    # now-empty contact line does.
    assert "app-footer" in resp.text
    assert "help@footer-clear.example.com" not in resp.text


def test_the_license_page_without_the_file(client, monkeypatch, tmp_path):
    """The Docker image once left LICENSE out and the page answered 500 (found by ZAP, 1.25)."""
    from openclear.web.routers import license as license_router

    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in client.get("/ui/license").text
    monkeypatch.setattr(license_router, "_LICENSE_PATH", tmp_path / "missing")
    page = client.get("/ui/license")
    assert page.status_code == 200 and "gnu.org/licenses/agpl-3.0.html" in page.text


def test_the_license_line_has_the_notices_and_the_source(client, tenant_factory):
    """AGPL-3.0 §5(d) and §13 (UI_PLAN.md UI-D9): copyright, no warranty, the license, the source."""
    tenant, _account, users = tenant_factory.make(slug="footer-agpl")
    _login(client, tenant.slug, users["admin"].email)
    line = client.get("/ui/").text.split('class="app-footer-license"')[1].split("</p>")[0]
    assert "© 2026 Chaffed" in line and "no warranty" in line and 'href="/ui/license"' in line and "Source code" in line
    page = client.get("/ui/license").text
    assert "without any" in page and "warranty" in page and "github.com/chaffed/OpenClear" in page


def test_the_third_party_notices_are_linked_and_served(client, monkeypatch, tmp_path):
    """plans/THIRD_PARTY_PLAN.md TP2: the notices their licenses ask for travel with OpenClear."""
    from openclear.web.routers import license as license_router

    assert 'href="/ui/license/third-party"' in client.get("/ui/license").text
    page = client.get("/ui/license/third-party")
    assert page.status_code == 200 and "Third-party notices" in page.text and "Knut Sveidqvist" in page.text  # mermaid's
    monkeypatch.setattr(license_router, "_NOTICES_PATH", tmp_path / "missing")
    missing = client.get("/ui/license/third-party")
    assert missing.status_code == 200 and "THIRD_PARTY_NOTICES.md" in missing.text


def test_the_image_carries_the_notices_beside_the_license():
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    final = dockerfile.rsplit("\nFROM ", 1)[-1]
    assert "COPY --chown=openclear:openclear LICENSE THIRD_PARTY_NOTICES.md ./" in final


def test_the_image_stores_each_file_once():
    """plans/HOUSEKEEPING_PLAN.md HK5: a recursive chown in a layer of its own copies every file it
    touches, which stored the venv twice (2.4 GB; 1.4 GB without). Ownership is set by COPY --chown."""
    from pathlib import Path

    final = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text().rsplit("\nFROM ", 1)[-1]
    assert "chown -R" not in final
    copies = [line for line in final.splitlines() if line.startswith("COPY ")]
    assert copies and all(line.startswith("COPY --chown=openclear:openclear ") for line in copies)
