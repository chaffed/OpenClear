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


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    return create_engine(
        settings.database_url,
        connect_args=_connect_args(settings.database_url),
        pool_pre_ping=True,
        future=True,
    )


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
