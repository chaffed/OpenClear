# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Run a scheduled job on at most one app instance at a time.

Every instance that enables a scheduler flag starts its own in-process APScheduler, so two
instances sharing one database would each run every job: two retrains, two dropbox scans
racing to import the same file, two notification sends. On PostgreSQL each job run first
takes a session-level advisory lock keyed on the job's id (`pg_try_advisory_lock`, which
never waits). The instance that gets it runs the job; any other instance skips that tick
and tries again on its next one. Postgres drops the lock automatically if the holder's
connection dies, so a crashed instance can't leave a job stuck.

SQLite and SQL Server have no equivalent here, so on them the job simply runs: those
deployments must stay a single process (see README, "Running more than one instance").
"""

import hashlib
import logging
from collections.abc import Callable

from sqlalchemy import Engine, text

from openclear.db.session import get_engine

logger = logging.getLogger(__name__)


def lock_key(job_id: str) -> int:
    """A stable signed 64-bit key for pg_try_advisory_lock, the same on every instance."""
    digest = hashlib.sha256(f"pospay.scheduler:{job_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def run_exclusively(job_id: str, job: Callable[[], None], *, engine: Engine | None = None) -> bool:
    """Run `job` unless another instance is already running it. Returns whether it ran."""
    engine = engine or get_engine()
    if engine.dialect.name != "postgresql":
        job()
        return True

    key = lock_key(job_id)
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        # Commit so this connection sits idle, not "idle in transaction", while the job
        # runs. The lock is session-level, so it survives the commit.
        conn.commit()
        if not acquired:
            logger.info("Skipping scheduled job %s: another instance is running it", job_id)
            return False
        try:
            job()
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                conn.commit()
            except Exception:  # noqa: BLE001 -- never return a connection that may still hold the lock
                logger.exception("Couldn't release the lock for scheduled job %s; discarding the connection", job_id)
                conn.invalidate()
    return True
