# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A durable background-job queue in the app's own database (SCALE_PLAN.md S3).

Anything slow or CPU-heavy that a request or the scheduler starts (OCR, a data export, a
bank's dropbox scan, retraining a model) is a `Job` row here, not work done inside the
web request. Workers claim due jobs, run the registered handler for the job's `kind`
with a session bound to the job's bank (so Postgres row-level security applies), and
record the result. A failure is retried with back-off; a worker that dies mid-job stops
sending heartbeats and its job is queued again.

Where jobs run is `config.Settings.worker_mode`:
- "in_process": a worker thread inside each web process (start_in_process_worker, from
  main.py). Nothing else to deploy, the default.
- "external": no worker in the web process; run `openclear-worker` (workers/run.py) as
  its own process(es) and scale those independently.
- "inline": each job runs right after whatever queued it. Used by the test suite, so a
  test sees the job's effect as soon as its request returns.

Claiming is safe with any number of workers: the claim is a conditional update
(`status = 'queued'`), and on databases that support it (Postgres, SQL Server) the
candidate row is read with SKIP LOCKED so workers don't wait on each other.
"""

import logging
import os
import random
import socket
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.job import Job, JobStatus

logger = logging.getLogger(__name__)

Handler = Callable[[Session, Job], None]
_HANDLERS: dict[str, Handler] = {}

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_MAX_SECONDS = 3600


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Registers the function that runs jobs of `kind` (see workers/job_handlers.py)."""

    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return register


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def enqueue(
    session: Session,
    kind: str,
    *,
    tenant_id: uuid.UUID | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
) -> Job:
    """Adds a job to the caller's transaction; it becomes visible to workers when the
    caller commits, so a job never runs against data that was rolled back. With a
    `dedupe_key`, an identical job that's still waiting or running is returned instead
    of adding another."""
    if dedupe_key is not None:
        existing = session.execute(
            select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    job = Job(
        kind=kind,
        tenant_id=tenant_id,
        payload=payload or {},
        dedupe_key=dedupe_key,
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=get_settings().job_max_attempts,
        run_after=run_after or _now(),
    )
    session.add(job)
    session.flush()
    return job


def requeue_stale(session: Session) -> int:
    """Running jobs whose worker stopped sending heartbeats (crashed, killed, redeployed)
    go back in the queue, or to FAILED if they've used every attempt."""
    cutoff = _now() - timedelta(seconds=get_settings().job_stale_after_seconds)
    stale = (Job.status == JobStatus.RUNNING, Job.heartbeat_at < cutoff)
    given_up = session.execute(
        update(Job).where(*stale, Job.attempts >= Job.max_attempts).values(
            status=JobStatus.FAILED, locked_by=None, finished_at=_now(), last_error="Worker stopped responding (no heartbeat)."
        )
    ).rowcount
    requeued = session.execute(
        update(Job).where(*stale).values(status=JobStatus.QUEUED, locked_by=None, run_after=_now())
    ).rowcount
    session.commit()
    if given_up or requeued:
        logger.warning("Recovered stale jobs: %d queued again, %d failed", requeued, given_up)
    return requeued + given_up


def claim(session: Session, worker_id: str) -> Job | None:
    """The next due job, now RUNNING and owned by `worker_id`; None if nothing is due."""
    now = _now()
    candidates = (
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
        .order_by(Job.run_after, Job.created_at)
        .limit(5)
    )
    if session.get_bind().dialect.name != "sqlite":
        candidates = candidates.with_for_update(skip_locked=True)
    for job_id in session.execute(candidates).scalars().all():
        taken = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
            .values(
                status=JobStatus.RUNNING, locked_by=worker_id, heartbeat_at=now, started_at=now, attempts=Job.attempts + 1
            )
        ).rowcount
        if taken == 1:
            session.commit()
            return session.get(Job, job_id)
    session.commit()
    return None


class _Heartbeat:
    """Keeps a long-running job's heartbeat_at fresh from a side thread, on its own
    connection, so a slow job isn't mistaken for a dead one."""

    def __init__(self, engine: Engine, job_id: uuid.UUID, worker_id: str) -> None:
        self._engine, self._job_id, self._worker_id = engine, job_id, worker_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"job-heartbeat-{job_id}", daemon=True)
        self._interval = max(1.0, get_settings().job_stale_after_seconds / 5)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with Session(bind=self._engine) as session:
                    session.execute(
                        update(Job).where(Job.id == self._job_id, Job.locked_by == self._worker_id).values(heartbeat_at=_now())
                    )
                    session.commit()
            except Exception:  # noqa: BLE001 -- a missed heartbeat only risks a harmless requeue
                logger.exception("Heartbeat failed for job %s", self._job_id)


def _backoff(attempts: int) -> timedelta:
    seconds = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** max(0, attempts - 1))
    return timedelta(seconds=seconds * random.uniform(0.8, 1.2))


def _execute(engine: Engine, job: Job, worker_id: str) -> None:
    from openclear.workers import job_handlers  # noqa: F401 -- registers every handler

    handler_fn = _HANDLERS.get(job.kind)
    job_id, tenant_id, kind = job.id, job.tenant_id, job.kind
    error: str | None = None
    session = Session(bind=engine)
    try:
        if handler_fn is None:
            raise LookupError(f"No handler registered for job kind {kind!r}")
        if tenant_id is not None:
            bind_session_to_tenant(session, tenant_id)
        # Inline mode (the test suite) shares one connection, so no side-thread heartbeat.
        if get_settings().worker_mode == "inline":
            handler_fn(session, session.get(Job, job_id))
        else:
            with _Heartbeat(engine, job_id, worker_id):
                handler_fn(session, session.get(Job, job_id))
        session.commit()
    except Exception:  # noqa: BLE001 -- recorded on the job and retried; never kills the worker
        session.rollback()
        error = traceback.format_exc()[-4000:]
        logger.exception("Job %s (%s) failed", job_id, kind)
    finally:
        session.close()

    with Session(bind=engine) as control:
        row = control.get(Job, job_id)
        row.locked_by = None
        if error is None:
            row.status, row.finished_at, row.last_error = JobStatus.SUCCEEDED, _now(), None
        elif row.attempts >= row.max_attempts:
            row.status, row.finished_at, row.last_error = JobStatus.FAILED, _now(), error
        else:
            row.status, row.run_after, row.last_error = JobStatus.QUEUED, _now() + _backoff(row.attempts), error
        control.commit()


def run_one(engine: Engine, worker_id: str | None = None) -> bool:
    """Claims and runs one due job. Returns whether there was one."""
    worker_id = worker_id or _worker_id()
    with Session(bind=engine) as session:
        requeue_stale(session)
        job = claim(session, worker_id)
        if job is None:
            return False
        session.expunge(job)
    _execute(engine, job, worker_id)
    return True


def drain(engine: Engine, max_jobs: int = 1000) -> int:
    """Runs due jobs until none are left (or `max_jobs`). Returns how many ran."""
    count = 0
    while count < max_jobs and run_one(engine):
        count += 1
    return count


# --- Starting work after queuing it -------------------------------------------------------

_wake = threading.Event()


def jobs_queued(engine: Engine, background_tasks=None) -> None:
    """Call after committing newly queued jobs. Inline mode runs them now (after the
    response, if a request's BackgroundTasks is given); in-process mode wakes the worker
    thread instead of waiting for its next poll; external mode does nothing."""
    mode = get_settings().worker_mode
    if mode == "inline":
        if background_tasks is not None:
            background_tasks.add_task(drain, engine)
        else:
            drain(engine)
    elif mode == "in_process":
        _wake.set()


# --- The in-process worker thread -----------------------------------------------------------

_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop(engine: Engine, stop: threading.Event) -> None:
    worker_id = _worker_id()
    logger.info("Background worker %s started", worker_id)
    while not stop.is_set():
        try:
            if run_one(engine, worker_id):
                continue
        except Exception:  # noqa: BLE001 -- e.g. the database is briefly unreachable
            logger.exception("Background worker loop error")
        _wake.wait(get_settings().worker_poll_seconds)
        _wake.clear()
    logger.info("Background worker %s stopped", worker_id)


def start_in_process_worker(engine: Engine) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(engine, _stop), name="openclear-worker", daemon=True)
    _thread.start()


def stop_in_process_worker() -> None:
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout=10)
    _thread = None


def run_forever(engine: Engine, stop: threading.Event) -> None:
    """The external worker's loop (workers/run.py)."""
    _loop(engine, stop)
