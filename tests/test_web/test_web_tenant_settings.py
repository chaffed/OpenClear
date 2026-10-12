# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.auth.security import decode_token
from openclear.config import get_settings
from tests import image_helpers
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_settings_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/settings", follow_redirects=False)
    assert resp.status_code == 403


def test_update_name_and_color(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-name-color")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings",
        data={"csrf_token": csrf, "name": "Renamed Corp", "accent_color": "#112233"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    page = client.get("/ui/")
    assert "Renamed Corp" in page.text
    assert "#112233" in page.text


def test_update_contact_info_persists_and_renders_in_form(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-contact")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/contact",
        data={
            "csrf_token": csrf,
            "support_email": "help@web-settings-contact.example.com",
            "support_phone": "(555) 222-3333",
            "website": "https://web-settings-contact.example.com",
            "address_line1": "1 Main St",
            "address_line2": "",
            "city": "Springfield",
            "state": "IL",
            "postal_code": "62701",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    page = client.get("/ui/settings")
    assert "help@web-settings-contact.example.com" in page.text
    assert "(555) 222-3333" in page.text
    assert "1 Main St" in page.text


def test_update_contact_info_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-contact-forbidden")
    csrf = _login(client, tenant.slug, users["viewer"].email)

    resp = client.post(
        "/ui/settings/contact",
        data={"csrf_token": csrf, "support_email": "x@example.com", "support_phone": "", "website": "",
              "address_line1": "", "address_line2": "", "city": "", "state": "", "postal_code": ""},
    )
    assert resp.status_code == 403


def test_settings_shows_auto_import_status_disabled_by_default(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-auto-import-disabled")
    _login(client, tenant.slug, users["admin"].email)

    page = client.get("/ui/settings/auto-import")
    assert page.status_code == 200
    assert "disabled" in page.text
    assert "No files auto-imported yet" in page.text


def test_settings_shows_recent_auto_imports(client, db_session, tenant_factory):
    from openclear.domain.bulk_upload_file import BulkUploadKind, BulkUploadSource
    from openclear.services import bulk_upload_file_service

    tenant, _account, users = tenant_factory.make(slug="web-settings-auto-import-recent")
    bulk_upload_file_service.record_uploaded_file(
        db_session, tenant.id, kind=BulkUploadKind.ISSUED_ITEMS, filename="dropped.csv", content_type="text/csv",
        data=b"account_number,check_number\n1,2\n", uploaded_by_user_id=None, source=BulkUploadSource.AUTO_IMPORT,
    )
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    page = client.get("/ui/settings/auto-import")
    assert page.status_code == 200
    assert "dropped.csv" in page.text
    assert "issued_items" in page.text


def test_upload_and_serve_logo_and_favicon(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-upload")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings",
        data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={
            "logo": ("logo.png", image_helpers.PNG, "image/png"),
            "favicon": ("favicon.ico", image_helpers.ICO, "image/x-icon"),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    logo_resp = client.get(f"/ui/branding/{tenant.slug}/logo")
    assert logo_resp.status_code == 200
    assert logo_resp.content == image_helpers.PNG
    assert logo_resp.headers["content-type"] == "image/png"

    favicon_resp = client.get(f"/ui/branding/{tenant.slug}/favicon")
    assert favicon_resp.status_code == 200
    assert favicon_resp.content == image_helpers.ICO

    nav_page = client.get("/ui/")
    assert f'/ui/branding/{tenant.slug}/logo' in nav_page.text
    assert f'/ui/branding/{tenant.slug}/favicon' in nav_page.text


def test_branding_assets_404_when_unset(client, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-settings-no-assets")
    resp = client.get(f"/ui/branding/{tenant.slug}/logo")
    assert resp.status_code == 404


def test_branding_assets_404_for_unknown_slug(client):
    resp = client.get("/ui/branding/does-not-exist/logo")
    assert resp.status_code == 404


def test_rejects_malformed_color_without_500(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-bad-color")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/settings", data={"csrf_token": csrf, "name": tenant.name, "accent_color": "nope"})
    assert resp.status_code == 422
    assert "not a valid hex color" in resp.text


def test_rejects_disallowed_image_type_without_500(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-bad-image")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings",
        data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={"logo": ("evil.pdf", b"not-an-image", "application/pdf")},
    )
    assert resp.status_code == 422
    assert "Upload a PNG, JPEG, or ICO image" in resp.text


def test_session_timeout_override_reflected_in_issued_token(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-session-timeout")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/session-timeout",
        data={"csrf_token": csrf, "access_token_expire_minutes": "5", "refresh_token_expire_minutes": "120"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Log back in (session-timeout only affects tokens minted after saving) and confirm
    # the new access token's exp claim reflects the override, not the global default.
    client.post("/ui/logout", data={"csrf_token": csrf})
    csrf = _login(client, tenant.slug, users["admin"].email)
    claims = decode_token(client.cookies.get("access_token"))
    assert abs((claims["exp"] - claims["iat"]) - 5 * 60) < 2


def test_session_timeout_blank_resets_to_global_default(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-session-timeout-reset")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post(
        "/ui/settings/session-timeout",
        data={"csrf_token": csrf, "access_token_expire_minutes": "5", "refresh_token_expire_minutes": "120"},
    )

    resp = client.post(
        "/ui/settings/session-timeout",
        data={"csrf_token": csrf, "access_token_expire_minutes": "", "refresh_token_expire_minutes": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    client.post("/ui/logout", data={"csrf_token": csrf})
    csrf = _login(client, tenant.slug, users["admin"].email)
    claims = decode_token(client.cookies.get("access_token"))
    settings = get_settings()
    assert abs((claims["exp"] - claims["iat"]) - settings.jwt_access_token_expire_minutes * 60) < 2


def test_session_timeout_rejects_non_positive_value(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-session-timeout-bad")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/session-timeout",
        data={"csrf_token": csrf, "access_token_expire_minutes": "0", "refresh_token_expire_minutes": ""},
    )
    assert resp.status_code == 422


def test_data_export_timeout_persists_override(client, db_session, tenant_factory):
    from openclear.domain.tenant import Tenant

    tenant, _account, users = tenant_factory.make(slug="web-settings-export-timeout")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/data-export-timeout",
        data={"csrf_token": csrf, "timeout_seconds": "900"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).data_export_timeout_seconds == 900


def test_data_export_timeout_blank_resets_to_global_default(client, db_session, tenant_factory):
    from openclear.domain.tenant import Tenant

    tenant, _account, users = tenant_factory.make(slug="web-settings-export-timeout-reset")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post("/ui/settings/data-export-timeout", data={"csrf_token": csrf, "timeout_seconds": "900"})

    resp = client.post(
        "/ui/settings/data-export-timeout",
        data={"csrf_token": csrf, "timeout_seconds": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).data_export_timeout_seconds is None


def test_data_export_timeout_rejects_non_positive_value(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-export-timeout-bad")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/settings/data-export-timeout", data={"csrf_token": csrf, "timeout_seconds": "0"})
    assert resp.status_code == 422


def test_branded_login_page_shows_name_and_logo(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-branded-login")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post(
        "/ui/settings",
        data={"csrf_token": csrf, "name": "Branded Login Co", "accent_color": ""},
        files={"logo": ("logo.png", image_helpers.PNG, "image/png")},
    )
    client.post("/ui/logout", data={"csrf_token": csrf})

    resp = client.get(f"/ui/login/{tenant.slug}")
    assert resp.status_code == 200
    assert "Branded Login Co" in resp.text
    assert f"/ui/branding/{tenant.slug}/logo" in resp.text
    assert f'value="{tenant.slug}"' in resp.text


def test_branded_login_page_shows_login_message(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-login-message")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post(
        "/ui/settings/messages",
        data={"csrf_token": csrf, "login_message": "**Maintenance** Saturday", "banner_message": ""},
    )
    client.post("/ui/logout", data={"csrf_token": csrf})

    resp = client.get(f"/ui/login/{tenant.slug}")
    assert resp.status_code == 200
    assert "<strong>Maintenance</strong>" in resp.text


def test_unbranded_login_falls_back_to_generic(client):
    resp = client.get("/ui/login/does-not-exist-at-all")
    assert resp.status_code == 200
    assert "OpenClear" in resp.text


def test_plain_login_page_still_works(client):
    resp = client.get("/ui/login")
    assert resp.status_code == 200


def test_settings_is_a_control_panel_and_no_longer_links_sso_settings(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-full-width")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/settings")
    assert resp.status_code == 200
    assert 'class="tiles"' in resp.text
    assert "card-narrow" not in resp.text
    assert "/ui/settings/sso" not in resp.text


def test_update_password_policy_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-password-policy")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/password-policy",
        data={
            "csrf_token": csrf, "min_length": "12", "require_uppercase": "true", "require_number": "true",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    from openclear.domain.tenant import Tenant

    updated = db_session.get(Tenant, tenant.id)
    assert updated.password_min_length == 12
    assert updated.password_require_uppercase is True
    assert updated.password_require_number is True
    assert updated.password_require_lowercase is False


def test_update_password_policy_rejects_non_positive_min_length(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-password-policy-invalid")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/settings/password-policy", data={"csrf_token": csrf, "min_length": "0"})
    assert resp.status_code == 422
    assert "at least 1" in resp.text


def test_update_messages_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-messages")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/messages",
        data={"csrf_token": csrf, "login_message": "Login notice", "banner_message": "Banner notice"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    from openclear.domain.tenant import Tenant

    updated = db_session.get(Tenant, tenant.id)
    assert updated.login_message == "Login notice"
    assert updated.banner_message == "Banner notice"


def test_update_messages_rejects_overlong_input(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-messages-invalid")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/messages", data={"csrf_token": csrf, "login_message": "x" * 500_001, "banner_message": ""}
    )
    assert resp.status_code == 422
    assert "KB or smaller" in resp.text


def test_update_messages_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-messages-forbidden")
    csrf = _login(client, tenant.slug, users["preparer"].email)

    resp = client.post(
        "/ui/settings/messages", data={"csrf_token": csrf, "login_message": "x", "banner_message": "y"}, follow_redirects=False,
    )
    assert resp.status_code == 403


def test_banner_message_shows_on_every_page_once_logged_in(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-banner-display")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post("/ui/settings/messages", data={"csrf_token": csrf, "login_message": "", "banner_message": "Tenant _notice_"})

    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "<em>notice</em>" in resp.text
    assert 'data-banner-carousel' in resp.text
    # Only one message set -- no carousel dots needed.
    assert "banner-dot" not in resp.text


def test_no_banner_markup_when_no_messages_set(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-banner-none")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")
    assert "banner-carousel" not in resp.text


def test_svg_logo_is_rejected(client, tenant_factory):
    """SVG can carry script, and logos are served publicly from this origin."""
    tenant, _account, users = tenant_factory.make(slug="web-settings-svg")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings",
        data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={"logo": ("logo.svg", image_helpers.SVG, "image/svg+xml")},
    )

    assert resp.status_code == 422
    assert client.get(f"/ui/branding/{tenant.slug}/logo").status_code == 404


def test_logo_type_comes_from_the_file_not_the_browser(client, tenant_factory):
    """Script disguised with an image Content-Type is refused; a real JPEG declared as
    something else is stored and served as what it actually is."""
    tenant, _account, users = tenant_factory.make(slug="web-settings-sniff")
    csrf = _login(client, tenant.slug, users["admin"].email)

    disguised = client.post(
        "/ui/settings", data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={"logo": ("logo.png", b"<html><script>alert(1)</script></html>", "image/png")},
    )
    assert disguised.status_code == 422

    client.post(
        "/ui/settings", data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={"logo": ("logo.bin", image_helpers.JPEG, "application/octet-stream")},
    )
    served = client.get(f"/ui/branding/{tenant.slug}/logo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"


def test_branding_images_are_sandboxed(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-settings-sandbox")
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post(
        "/ui/settings", data={"csrf_token": csrf, "name": tenant.name, "accent_color": ""},
        files={"logo": ("logo.png", image_helpers.PNG, "image/png")},
    )

    resp = client.get(f"/ui/branding/{tenant.slug}/logo")

    assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "public" in resp.headers["cache-control"]


def test_previously_uploaded_svg_logo_is_no_longer_served(client, db_session, tenant_factory, tmp_path):
    tenant, _account, _users = tenant_factory.make(slug="web-settings-legacy-svg")
    legacy = tmp_path / "logo.svg"
    legacy.write_bytes(image_helpers.SVG)
    tenant.logo_path = str(legacy)
    tenant.logo_content_type = "image/svg+xml"
    db_session.commit()

    assert client.get(f"/ui/branding/{tenant.slug}/logo").status_code == 404
    assert f"/ui/branding/{tenant.slug}/logo" not in client.get(f"/ui/login/{tenant.slug}").text
