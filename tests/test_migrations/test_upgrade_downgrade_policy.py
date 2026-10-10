# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Codifies the upgrade/downgrade support policy (see README.md's "Upgrade and downgrade
support" section and migrations/version_history.py): upgrading is always supported, from
any prior version; downgrading is only supported up to 2 minor versions back, never
across a major version boundary.

Also carries regression coverage for a specific class of bug found while establishing
this policy: a migration that backfills a new enum-typed column's *existing* rows with a
lowercase string literal (matching the Python enum's `.value`) instead of the uppercase
member *name* this app's enum columns actually expect on read (confirmed: no
`values_callable` configured anywhere, so SQLAlchemy's default enum serialization applies
everywhere). Each bug of this shape inserts fine and then raises `LookupError` the moment
anything reads the row back through the ORM -- exactly the kind of thing "upgrading is
fully supported" must guarantee against.
"""

import sqlite3
import tomllib
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from migrations.version_history import VERSION_HISTORY, minor_versions_back
from openclear.config import get_settings
from openclear.db.session import reset_engine_cache

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _current_app_version() -> str:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """A throwaway, file-backed sqlite db -- migrations/env.py reads the URL from
    openclear's own (lru_cache'd) settings, so the env var must be set and the cache
    cleared before Alembic's Config is ever touched, same "before any `from openclear...`
    import" ordering scripts/launcher.py's own doc comments call out."""
    db_path = tmp_path / "migration_policy.db"
    monkeypatch.setenv("OPENCLEAR_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine_cache()
    yield db_path
    get_settings.cache_clear()
    reset_engine_cache()


def test_full_upgrade_from_base_succeeds(scratch_db):
    command.upgrade(_alembic_config(), "head")


def test_upgrade_does_not_disable_other_loggers(scratch_db):
    """Regression test: migrations/env.py's fileConfig(alembic.ini) call defaults to
    disable_existing_loggers=True, which silently sets .disabled = True on every Python
    logger that already exists at that point and isn't explicitly named in alembic.ini's
    [loggers] section -- e.g. any openclear.* module logger created by an import that ran
    before this migration did. That's a standard fileConfig() gotcha (confirmed directly
    by reproducing it before the fix), not something alembic.ini's [loggers] section is
    meant to enumerate every application logger to avoid. Once disabled, a logger stays
    disabled for the rest of the process -- caught here because a real caller
    (ocr/factory.py's stub-provider warning) depends on its own logger actually firing."""
    import logging

    logger = logging.getLogger("openclear.some_arbitrary_module_not_in_alembic_ini")
    assert logger.disabled is False

    command.upgrade(_alembic_config(), "head")

    assert logger.disabled is False


class TestMinorVersionsBack:
    HISTORY = {
        "1.0.0": "revA",
        "1.1.0": "revB",
        "1.2.0": "revC",
        "1.3.0": "revD",
        "2.0.0": "revE",
    }

    def test_two_back_within_same_major(self):
        assert minor_versions_back(self.HISTORY, "1.3.0", 2) == "revB"

    def test_not_enough_history_returns_none(self):
        assert minor_versions_back(self.HISTORY, "1.1.0", 2) is None

    def test_never_crosses_a_major_version_boundary(self):
        assert minor_versions_back(self.HISTORY, "2.0.0", 2) is None

    def test_patch_release_does_not_count_as_its_own_step_back(self):
        history = {**self.HISTORY, "1.2.5": "revC2"}
        # 1 back from 1.3.0 is minor 1.2 -- and the *latest* patch recorded for that
        # minor (1.2.5's revC2), not the original 1.2.0 entry.
        assert minor_versions_back(history, "1.3.0", 1) == "revC2"


def test_downgrade_two_minor_versions_supported(scratch_db):
    current_version = _current_app_version()
    target_revision = minor_versions_back(VERSION_HISTORY, current_version, 2)
    if target_revision is None:
        pytest.skip(
            f"Not enough recorded release history yet to test downgrading 2 minor versions back from "
            f"{current_version} (see migrations/version_history.py) -- expected right after this policy lands."
        )

    command.upgrade(_alembic_config(), "head")
    command.downgrade(_alembic_config(), target_revision)


# --- Regression coverage for the four enum-casing bugs found while auditing the chain ---


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def test_payment_network_settlement_timing_survives_migration_and_orm_read(scratch_db):
    command.upgrade(_alembic_config(), "9327972d0952")  # inserts the check/ach rows itself

    from openclear.db.session import get_session_factory
    from openclear.domain.payment_network import PaymentNetwork

    session = get_session_factory()()
    try:
        rows = session.query(PaymentNetwork).all()
        assert {r.code for r in rows} == {"check", "ach"}
    finally:
        session.close()


def test_bulk_upload_file_source_survives_migration_and_orm_read(scratch_db):
    command.upgrade(_alembic_config(), "a3b4c5d6e7f8")
    conn = _connect(scratch_db)
    row_id, tenant_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        "INSERT INTO bulk_upload_file (id, tenant_id, kind, original_filename, storage_path, size_bytes, "
        "sha256_hex, signature_hex, uploaded_by_user_id) VALUES (?, ?, 'ISSUED_ITEMS', 'x.csv', '/tmp/x', 10, 'a', 'b', ?)",
        (row_id, tenant_id, user_id),
    )
    conn.commit()
    conn.close()

    # Upgrade all the way: the ORM model below is today's, so reading through it needs
    # every later column too (b1c2d3e4f5a6 added `source`).
    command.upgrade(_alembic_config(), "head")

    from openclear.db.session import get_session_factory
    from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadSource, BulkUploadStatus

    session = get_session_factory()()
    try:
        row = session.query(BulkUploadFile).one()
        assert row.source == BulkUploadSource.MANUAL
        assert row.status == BulkUploadStatus.REJECTED  # no result counts: it never finished
    finally:
        session.close()


def test_exception_item_source_survives_migration_and_orm_read(scratch_db):
    command.upgrade(_alembic_config(), "d4e5f6a7b8c9")
    conn = _connect(scratch_db)
    row_id, tenant_id, source_item_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        "INSERT INTO exception_item (id, tenant_id, network_code, source_item_id, exception_types, status) "
        "VALUES (?, ?, 'check', ?, 'x', 'OPEN')",
        (row_id, tenant_id, source_item_id),
    )
    conn.commit()
    conn.close()

    # Upgrade all the way (not just to e5f6a7b8c9d0, the migration that added `source`):
    # the ORM model below is today's, so reading through it needs every later column too.
    command.upgrade(_alembic_config(), "head")

    from openclear.db.session import get_session_factory
    from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource

    session = get_session_factory()()
    try:
        row = session.query(ExceptionItem).one()
        assert row.source == ExceptionItemSource.LIVE
    finally:
        session.close()


def test_decision_source_survives_migration_and_orm_read(scratch_db):
    command.upgrade(_alembic_config(), "e5f6a7b8c9d0")
    conn = _connect(scratch_db)
    row_id, tenant_id, exception_item_id, user_id = (uuid.uuid4().hex for _ in range(4))
    conn.execute(
        "INSERT INTO decision (id, tenant_id, exception_item_id, outcome, reason_code, decided_by_user_id) "
        "VALUES (?, ?, ?, 'PAY', 'x', ?)",
        (row_id, tenant_id, exception_item_id, user_id),
    )
    conn.commit()
    conn.close()

    # All the way up: the ORM model below is today's (f7a8b9c0d1e2 added `source`).
    command.upgrade(_alembic_config(), "head")

    from openclear.db.session import get_session_factory
    from openclear.domain.decision import Decision, DecisionSource

    session = get_session_factory()()
    try:
        row = session.query(Decision).one()
        assert row.source == DecisionSource.HUMAN
    finally:
        session.close()


def _insert_with_required_defaults(conn, table, values):
    """Inserts a row, filling any other NOT NULL column that has no database default with
    a zero value, so a test stays valid as later migrations add required columns."""
    row = dict(values)
    for _cid, name, _type, notnull, default, pk in conn.execute(f"PRAGMA table_info({table})"):
        if notnull and default is None and not pk and name not in row:
            row[name] = 0
    columns = ", ".join(row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))


def test_ml_model_choice_migration_backfills_model_owners_and_keeps_banks_on_shared(scratch_db):
    """a9c4e2f1d7b5: customer models get their bank as tenant_id, shared models stay
    ownerless, every existing bank stays on the shared model with no 90-day lock, and
    existing platform keys stay usage-only."""
    command.upgrade(_alembic_config(), "f2a9d4c7e1b3")
    conn = _connect(scratch_db)
    tenant_id, customer_id = uuid.uuid4().hex, uuid.uuid4().hex
    shared_model_id, customer_model_id, key_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    _insert_with_required_defaults(conn, "tenant", {"id": tenant_id, "name": "Old Bank", "slug": "old-bank"})
    _insert_with_required_defaults(conn, "customer", {"id": customer_id, "tenant_id": tenant_id, "customer_number": "C1", "name": "Cust"})
    for model_id, owner in ((shared_model_id, None), (customer_model_id, customer_id)):
        conn.execute(
            "INSERT INTO ml_model (id, network_code, customer_id, version, algorithm, artifact_path, trained_from_decision_count, status) "
            "VALUES (?, 'check', ?, 'v1', 'logistic_regression', '/tmp/x.joblib', 10, 'ACTIVE')",
            (model_id, owner),
        )
    conn.execute("INSERT INTO platform_api_key (id, name, key_hash) VALUES (?, 'billing', 'abc')", (key_id,))
    conn.commit()
    conn.close()

    command.upgrade(_alembic_config(), "head")

    conn = _connect(scratch_db)
    owners = dict(conn.execute("SELECT id, tenant_id FROM ml_model").fetchall())
    tenant = conn.execute("SELECT ml_model_source, ml_private_switch_allowed_at FROM tenant").fetchone()
    scopes = conn.execute("SELECT scopes FROM platform_api_key").fetchone()[0]
    conn.close()
    assert owners[customer_model_id] == tenant_id
    assert owners[shared_model_id] is None
    assert tenant == ("SHARED", None)
    assert scopes == '["usage"]'


def test_artifact_sha256_migration_pins_existing_files_and_leaves_missing_ones_null(scratch_db, tmp_path):
    """c3e8f1a5d9b2: a model whose file exists gets that file's SHA-256; one whose file is
    gone stays NULL, which ml/registry.py refuses to load."""
    import hashlib

    command.upgrade(_alembic_config(), "b7d3e9a2c4f1")
    present = tmp_path / "present.joblib"
    present.write_bytes(b"model bytes")
    present_id, missing_id = uuid.uuid4().hex, uuid.uuid4().hex
    conn = _connect(scratch_db)
    for model_id, path in ((present_id, str(present)), (missing_id, str(tmp_path / "missing.joblib"))):
        conn.execute(
            "INSERT INTO ml_model (id, network_code, version, algorithm, artifact_path, trained_from_decision_count, status) "
            "VALUES (?, 'check', 'v1', 'logistic_regression', ?, 10, 'ACTIVE')",
            (model_id, path),
        )
    conn.commit()
    conn.close()

    command.upgrade(_alembic_config(), "head")

    conn = _connect(scratch_db)
    digests = dict(conn.execute("SELECT id, artifact_sha256 FROM ml_model").fetchall())
    conn.close()
    assert digests[present_id] == hashlib.sha256(b"model bytes").hexdigest()
    assert digests[missing_id] is None


def test_audit_sequence_migration_numbers_entries_by_chain_links_not_timestamps(scratch_db):
    """f7b2d4e6a8c1: existing entries are numbered by following prev_entry_hash, so entries
    that share a timestamp still get their true chain order."""
    command.upgrade(_alembic_config(), "e5a1c7d3b9f2")
    conn = _connect(scratch_db)
    tenant_id = uuid.uuid4().hex
    _insert_with_required_defaults(conn, "tenant", {"id": tenant_id, "name": "Old Bank", "slug": "old-bank-audit"})
    # Chain order is first -> second -> third, but all share one timestamp and their ids
    # sort in the opposite order.
    ids = {"first": "c" * 32, "second": "b" * 32, "third": "a" * 32}
    prev = None
    for name in ("first", "second", "third"):
        conn.execute(
            "INSERT INTO audit_log_entry (id, tenant_id, occurred_at, channel, action, summary, resource_type, prev_entry_hash, entry_hash) "
            "VALUES (?, ?, '2026-01-01 00:00:00', 'WEB', 'x', ?, 'tenant', ?, ?)",
            (ids[name], tenant_id, name, prev, f"hash-{name}"),
        )
        prev = f"hash-{name}"
    conn.commit()
    conn.close()

    command.upgrade(_alembic_config(), "head")

    conn = _connect(scratch_db)
    order = [row[0] for row in conn.execute("SELECT summary FROM audit_log_entry ORDER BY sequence")]
    conn.close()
    assert order == ["first", "second", "third"]


def test_downgrading_past_aes_gcm_leaves_secrets_readable(scratch_db):
    """1.9 stores secrets as AES-256-GCM (auth/crypto.py); 1.8.0 reads only Fernet, so the
    downgrade converts them back."""
    from openclear.auth import crypto

    command.upgrade(_alembic_config(), "head")
    conn = _connect(scratch_db)
    tenant_id, endpoint_id = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute("INSERT INTO tenant (id, name, slug, require_dual_control, stale_date_threshold_days, is_active, is_demo, sod_mode) "
                 "VALUES (?, 'Bank', 'bank', 0, 180, 1, 0, 'warn')", (tenant_id,))
    conn.execute("INSERT INTO webhook_endpoint (id, tenant_id, url, event_types, secret_encrypted, is_active) VALUES (?, ?, ?, '[]', ?, 1)",
                 (endpoint_id, tenant_id, "https://hooks.example.com", crypto.encrypt_secret("whsec_test")))
    conn.commit()
    conn.close()

    command.downgrade(_alembic_config(), "e6a8c0e2a4c7")  # 1.8.0
    conn = _connect(scratch_db)
    [value] = conn.execute("SELECT secret_encrypted FROM webhook_endpoint").fetchone()
    conn.close()
    assert not value.startswith("v2:") and crypto._fernet(get_settings().sso_encryption_key).decrypt(value.encode()) == b"whsec_test"
