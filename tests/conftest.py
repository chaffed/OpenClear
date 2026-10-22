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
# Background jobs run right after whatever queued them (workers/jobs.py), so a test sees
# a job's effect as soon as its request returns.
os.environ.setdefault("OPENCLEAR_WORKER_MODE", "inline")
os.environ.setdefault("OPENCLEAR_REENCRYPT_SECRETS_AT_STARTUP", "false")  # tests call reencrypt_all themselves
os.environ.setdefault("OPENCLEAR_RELEASE_FEED_URL", "")  # no network: tests give the software currency check its feed
os.environ.setdefault("OPENCLEAR_SOC2_MAPPING_PATH", "")  # never a developer's own AICPA mapping: tests make their own

import uuid
from collections.abc import Generator

import bcrypt
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

# Passwords hashed at bcrypt's lowest cost (4) rather than its default (12): each
# TenantFactory.make() hashes four, and at full cost that was half the suite's time.
# Hashes made at any cost still verify, so only the time changes.
_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _gensalt(rounds, prefix)


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
    # No default cut-off in tests: decisions are final when made unless a test sets a cut-off
    # (tests/test_decisions). Production refuses to start without one (config.assert_production_safe).
    monkeypatch.setenv("OPENCLEAR_DEFAULT_DECISION_CUTOFF", "")
    # Every test signs in from the test client's one address; tests of the sign-in limit set their own.
    monkeypatch.setenv("OPENCLEAR_LOGIN_ATTEMPTS_PER_MINUTE", "10000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_security_event_sampling():
    from openclear.services import security_events

    security_events.reset_sampling_for_tests()
    yield


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
_BASE_TEST_DATABASE_URL = os.environ.get("OPENCLEAR_TEST_DATABASE_URL") or os.environ.get("POSPAY_TEST_DATABASE_URL")  # the pre-1.6 name


def _per_worker(url: str | None) -> str | None:
    """Run in parallel (pytest -n, pytest-xdist), each worker gets its own Postgres database,
    `<name>_gw0`, `<name>_gw1` and so on, created on first use, since each run wipes and
    rebuilds the schema of the database it uses."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not url or not worker:
        return url
    from sqlalchemy.engine import make_url

    base = make_url(url)
    name = f"{base.database}_{worker}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


TEST_DATABASE_URL = _per_worker(_BASE_TEST_DATABASE_URL)


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
def session_factory(engine) -> Generator[sessionmaker[Session], None, None]:
    from openclear.services import security_events

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Rows written in their own transaction (security events, control status) go to the test database.
    security_events.use_session_factory(factory)
    yield factory
    security_events.use_session_factory(None)


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
        from openclear.services import check_return_reason_service

        check_return_reason_service.seed_default_check_return_reasons(self.session, tenant.id)

        users = {}
        for group_name, group in groups.items():
            if group.system_role:  # built in ("No access yet"): nobody joins it by default
                continue
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
    # Security events written in their own transaction (a refusal) go to the test database too.
    from openclear.services import security_events

    security_events.use_session_factory(session_factory)
    yield app
    security_events.use_session_factory(None)


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


def approve_provider_changes(session: Session) -> None:
    """Have a platform operator approve every service-provider change waiting (DUAL_CONTROL_PLAN.md
    DC4), as the operator view would: for tests that set up fees or the shared model through the
    platform API, which now waits for a person."""
    from openclear.auth.security import hash_password
    from openclear.domain.user import User
    from openclear.services import change_request_service

    approver = session.query(User).filter(User.email == "approving.operator@platform.example").first()
    if approver is None:
        approver = User(email="approving.operator@platform.example", hashed_password=hash_password(TenantFactory.PASSWORD), platform_role="operator")
        session.add(approver)
        session.flush()
    for change in change_request_service.pending(session, None):
        change_request_service.decide(session, None, change.id, approve=True, user_id=approver.id)
    session.commit()


@pytest.fixture
def email_configured(monkeypatch):
    """Email counts as set up (notification_service.email_configured), for flows that refuse
    without it, such as an administrator's password reset link. Nothing is actually sent:
    notifications are only queued in tests."""
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
