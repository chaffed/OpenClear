# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Fixtures for running the app as a restricted Postgres role, so row-level security is
actually enforced (the rest of the suite connects as the owner/superuser, which RLS never
applies to). Only used with OPENCLEAR_TEST_DATABASE_URL set."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from openclear.db.session import get_db
from tests.conftest import TEST_DATABASE_URL

_APP_ROLE = "openclear_rls_app"


@pytest.fixture
def app_engine(engine):
    with engine.begin() as conn:
        # Roles belong to the whole server: parallel workers (each with its own database)
        # take turns creating it.
        conn.execute(text("SELECT pg_advisory_xact_lock(7300199)"))
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
    from openclear.main import create_app

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


@pytest.fixture
def app_session_factory(app_engine):
    return sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)
