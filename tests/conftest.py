# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import os

# Relaxes config.py::assert_production_safe's checks so the test suite can use the
# checked-in dev_keys/ signing keys and default SSO encryption key — set before any
# openclear import (mirrors scripts/launcher.py::_configure_run_env's own documented
# "before any `from openclear...` import" pattern, since Settings is @lru_cache'd).
# "development" (not a separate "test" value — config.Settings.environment only
# distinguishes production from everything else).
os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "development")

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from openclear.config import get_settings
from openclear.db.base import Base
from openclear.db.session import get_db
from openclear.domain.account import Account
from openclear.domain.payment_network import PaymentNetwork, SettlementTiming
from openclear.domain.tenant import Tenant
from openclear.services import ach_return_reason_service, security_group_service, user_service

# openclear.domain's __init__ imports every model module, fully populating Base.metadata
# before create_all — see its docstring for why this must be centralized in one place.
import openclear.domain  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_filesystem_settings(tmp_path, monkeypatch):
    """Every test gets its own throwaway directory for anything the app writes to disk
    (trained ML model artifacts, check-image uploads) — without this, running the suite
    leaves ml_artifacts/ behind in the project root exactly like the launcher's runtime
    state used to, since these paths default to plain relative paths meant for a real
    single-instance deployment, not test isolation."""
    monkeypatch.setenv("OPENCLEAR_ML_ARTIFACT_DIR", str(tmp_path / "ml_artifacts"))
    monkeypatch.setenv("OPENCLEAR_CHECK_IMAGE_STORAGE_DIR", str(tmp_path / "check_images"))
    monkeypatch.setenv("OPENCLEAR_TENANT_ASSET_STORAGE_DIR", str(tmp_path / "tenant_assets"))
    monkeypatch.setenv("OPENCLEAR_BULK_UPLOAD_STORAGE_DIR", str(tmp_path / "bulk_uploads"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """web/rate_limit.py's limiter is one process-wide singleton, shared by every test in
    this run -- and Starlette's TestClient reports the same synthetic client address for
    every request, so without resetting between tests, the global per-IP limit
    (main.py's middleware, applied to every request) would silently exhaust itself partway
    through the suite and every web test after that would start failing with 429s."""
    from openclear.web.rate_limit import limiter

    limiter._hits.clear()
    yield
    limiter._hits.clear()


# Set to a Postgres URL (e.g. postgresql+psycopg://openclear:openclear@localhost:5432/openclear_test)
# to run the suite against Postgres instead of in-memory SQLite. The database is WIPED:
# its public schema is dropped and rebuilt with `alembic upgrade head` once per run, so the
# migrations themselves are what's under test, and every test starts from empty tables.
TEST_DATABASE_URL = os.environ.get("OPENCLEAR_TEST_DATABASE_URL") or os.environ.get("POSPAY_TEST_DATABASE_URL")  # the pre-1.6 name


def _seed_payment_networks(engine) -> None:
    with Session(engine) as session:
        session.add_all(
            [
                PaymentNetwork(code="check", name="Check", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
                PaymentNetwork(code="ach", name="ACH", settlement_timing=SettlementTiming.ASYNC_REVIEWABLE),
            ]
        )
        session.commit()


@pytest.fixture(scope="session")
def _postgres_engine():
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    # migrations/env.py takes its URL from openclear's settings.
    previous_url = os.environ.get("OPENCLEAR_DATABASE_URL")
    os.environ["OPENCLEAR_DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    try:
        root = Path(__file__).resolve().parent.parent
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        command.upgrade(cfg, "head")
    finally:
        if previous_url is None:
            os.environ.pop("OPENCLEAR_DATABASE_URL", None)
        else:
            os.environ["OPENCLEAR_DATABASE_URL"] = previous_url
        get_settings.cache_clear()

    tables = [t.name for t in Base.metadata.sorted_tables]
    yield engine, tables
    engine.dispose()


@pytest.fixture
def engine(request):
    if TEST_DATABASE_URL:
        engine, tables = request.getfixturevalue("_postgres_engine")
        with engine.begin() as conn:
            quoted = ", ".join('"' + t + '"' for t in tables)
            conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
        _seed_payment_networks(engine)
        yield engine
        return

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    _seed_payment_networks(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


class TenantFactory:
    """Creates a tenant (with its 4 default security groups seeded) plus one user per
    default group, all with a known plaintext password, for tests that need a
    fully-authenticated multi-group scenario without repeating boilerplate. `make()`
    returns a dict of {group_name_lowercase: User} — e.g. users["admin"], users["preparer"]
    — mirroring the old fixed-role dict this replaces."""

    PASSWORD = "test-password-123"

    def __init__(self, session: Session):
        self.session = session

    def make(self, *, name: str = "Acme Corp", slug: str | None = None, require_dual_control: bool = False):
        slug = slug or f"acme-{uuid.uuid4().hex[:8]}"
        tenant = Tenant(name=name, slug=slug, require_dual_control=require_dual_control)
        self.session.add(tenant)
        self.session.flush()

        account = Account(tenant_id=tenant.id, account_number="0001", name="Operating")
        self.session.add(account)

        groups = security_group_service.seed_default_security_groups(self.session, tenant.id)
        ach_return_reason_service.seed_default_ach_return_reasons(self.session, tenant.id)

        users = {}
        for group_name, group in groups.items():
            user = user_service.create_user_with_membership(
                self.session,
                tenant.id,
                email=f"{group_name.lower()}@{slug}.example.com",
                password=self.PASSWORD,
                security_group_id=group.id,
            )
            users[group_name.lower()] = user

        self.session.commit()
        return tenant, account, users


@pytest.fixture
def tenant_factory(db_session) -> TenantFactory:
    return TenantFactory(db_session)


@pytest.fixture
def app(session_factory):
    from openclear.main import create_app

    app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def login_headers(client: TestClient, tenant_slug: str, email: str, password: str = TenantFactory.PASSWORD) -> dict:
    resp = client.post(
        "/api/v1/auth/login", json={"tenant_slug": tenant_slug, "email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
