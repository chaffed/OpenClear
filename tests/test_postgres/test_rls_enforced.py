# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Postgres row-level security, actually enforced. Runs only with POSPAY_TEST_DATABASE_URL
set (see tests/conftest.py).

The rest of the suite connects as the database owner/superuser, which RLS never applies
to. Here the app connects as a separate role with no special privileges, the way a real
deployment should, and a second bank's data sits in the same tables. The pool is left
on, so connections are reused between requests exactly as in production."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from pospay.db.session import get_db
from tests.conftest import TEST_DATABASE_URL, TenantFactory, login_headers

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs POSPAY_TEST_DATABASE_URL (Postgres)")

_APP_ROLE = "pospay_rls_app"


@pytest.fixture
def app_engine(engine):
    with engine.begin() as conn:
        conn.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN "
            f"CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{_APP_ROLE}' NOSUPERUSER NOBYPASSRLS; END IF; END $$"
        ))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"))
        conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))
    app_engine = create_engine(make_url(TEST_DATABASE_URL).set(username=_APP_ROLE, password=_APP_ROLE), pool_size=2)
    yield app_engine
    app_engine.dispose()


@pytest.fixture
def rls_client(app_engine):
    from pospay.main import create_app

    app = create_app()
    factory = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)

    def _get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as client:
        yield client


def _two_banks(tenant_factory):
    bank_a = tenant_factory.make(slug="rls-bank-a")
    bank_b = tenant_factory.make(slug="rls-bank-b")
    return bank_a, bank_b


def _count_accounts(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM account")).scalar()


def test_no_tenant_set_matches_no_rows_even_on_a_reused_connection(app_engine, tenant_factory):
    (bank_a, _account, _users), _bank_b = _two_banks(tenant_factory)

    with app_engine.connect() as conn:
        assert _count_accounts(conn) == 0
        conn.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(bank_a.id)})
        assert _count_accounts(conn) == 1
        conn.commit()
        # The setting is now '' (not NULL) on this connection: still no rows, not an error.
        assert _count_accounts(conn) == 0


def test_a_write_for_another_bank_is_rejected(app_engine, tenant_factory):
    (bank_a, _account, _users), (bank_b, _b_account, _b_users) = _two_banks(tenant_factory)

    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(bank_a.id)})
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text("INSERT INTO account (id, tenant_id, account_number, name) VALUES (gen_random_uuid(), :tid, '9', 'X')"),
                {"tid": str(bank_b.id)},
            )


def test_the_api_works_and_sees_only_its_own_bank(rls_client, tenant_factory):
    (bank_a, account_a, users_a), (bank_b, account_b, users_b) = _two_banks(tenant_factory)

    for bank, account, users in ((bank_a, account_a, users_a), (bank_b, account_b, users_b)):
        headers = login_headers(rls_client, bank.slug, users["preparer"].email)
        resp = rls_client.post(
            "/api/v1/paid-items",
            headers=headers,
            json={"account_id": str(account.id), "check_number": f"77{bank.slug[-1]}", "presented_amount": "10.00", "presented_date": "2026-01-10"},
        )
        assert resp.status_code == 201, resp.text

    headers_a = login_headers(rls_client, bank_a.slug, users_a["preparer"].email)
    exceptions = rls_client.get("/api/v1/exceptions", headers=headers_a)
    assert exceptions.status_code == 200, exceptions.text
    assert len(exceptions.json()) == 1  # bank A's not-in-file check only

    other_banks_account = rls_client.get(f"/api/v1/paid-items?account_id={account_b.id}", headers=headers_a)
    assert other_banks_account.status_code in (200, 404)
    if other_banks_account.status_code == 200:
        assert other_banks_account.json() == []


def test_the_web_ui_signs_in_and_renders_the_main_pages(rls_client, tenant_factory):
    (bank_a, _account, users_a), _bank_b = _two_banks(tenant_factory)

    rls_client.get("/ui/login")
    csrf = rls_client.cookies.get("csrf_token")
    login = rls_client.post(
        "/ui/login",
        data={"tenant_slug": bank_a.slug, "email": users_a["admin"].email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"},
        follow_redirects=False,
    )
    assert login.status_code == 303, login.text

    for path in ("/ui/", "/ui/exceptions", "/ui/paid-items", "/ui/issued-items", "/ui/accounts", "/ui/users", "/ui/settings"):
        # No redirects: a failed session check redirects to the login page, which is a 200 too.
        page = rls_client.get(path, follow_redirects=False)
        assert page.status_code == 200, (path, page.status_code, page.headers.get("location"))
        assert 'action="/ui/login"' not in page.text, path
