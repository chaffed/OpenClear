# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from openclear.config import Settings, get_settings


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        # SQLite connections are per-thread by default; FastAPI's sync routes run
        # in a threadpool, and each request gets its own Session/connection anyway,
        # so relaxing this check is safe here (no connection is shared across threads).
        return {"check_same_thread": False}
    return {}


def _pool_args(settings: Settings) -> dict:
    """Connection pool sizing (SCALE_PLAN.md S6). SQLite uses its own pool, which takes none
    of these."""
    if settings.database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    engine = create_engine(
        settings.database_url,
        connect_args=_connect_args(settings.database_url),
        pool_pre_ping=True,
        future=True,
        **_pool_args(settings),
    )
    if settings.database_url.startswith("sqlite") and ":memory:" not in settings.database_url:
        _sqlite_waits_and_shares(engine)
    return engine


def _sqlite_waits_and_shares(engine: Engine) -> None:
    """A single-process SQLite installation still has two writers, the web requests and the
    in-process worker thread (FULL_REVIEW_PLAN.md FR-D18, R9). Write-ahead logging lets readers
    carry on while one writes, and busy_timeout makes a writer wait up to five seconds for the
    other instead of failing at once with "database is locked"."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def reset_engine_cache() -> None:
    """Used by tests to force a fresh engine/session factory against a different DB URL."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
