# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Long requests as background jobs (WAF_PLAN.md W3; MASTER_PLAN.md M2). An edge network
gives up on a request after about 100 seconds (Cloudflare) or 120 (Akamai), and a worker
held that long is wasted anyway. So these run as jobs:

- **a replay** of a large file (services/mrm_service.py);
- **an evidence export** of a long period (services/evidence_service.py);
- **verifying the action log** (services/audit_log_service.py), which the Controls page
  then reads instead of walking the whole log on every load.

Each is a `control_run` (no new table), with its state in `report["state"]`: queued,
running, done or failed. Small ones still run at once, inside the request: a replay up to
`long_request_sync_bytes`, an evidence export of up to `long_request_sync_days`."""

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.control_run import ControlRun

JOB_KIND = "control_run.execute"
EVIDENCE = "evidence_export"
VERIFICATION = "audit_verification"
VERIFICATION_MAX_AGE = timedelta(hours=26)


def state(run: ControlRun) -> str:
    return (run.report or {}).get("state", "done")


def get_run(session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, kind: str | None = None) -> ControlRun | None:
    run = session.get(ControlRun, run_id)
    if run is None or run.tenant_id != tenant_id or (kind and run.kind != kind):
        return None
    return run


def _queue(session: Session, tenant_id: uuid.UUID, kind: str, summary: str, report: dict, user_id: uuid.UUID | None) -> ControlRun:
    from openclear.workers.jobs import enqueue

    run = ControlRun(tenant_id=tenant_id, kind=kind, passed=None, summary=summary[:500], report={**report, "state": "queued"},
                     requested_by_user_id=user_id)
    session.add(run)
    session.flush()
    enqueue(session, JOB_KIND, tenant_id=tenant_id, payload={"run_id": str(run.id)})
    return run


# --- Replay -------------------------------------------------------------------------------------

def start_replay(session: Session, tenant_id: uuid.UUID, *, filename: str, content: bytes, user_id: uuid.UUID | None,
                 channel: str) -> ControlRun:
    """A small file is replayed at once; a large one is stored and queued. The caller
    commits, then calls jobs_queued()."""
    from openclear import storage
    from openclear.services import mrm_service

    if not content:
        raise mrm_service.ReplayError("The file is empty.")
    if len(content) <= get_settings().long_request_sync_bytes:
        return mrm_service.replay(session, tenant_id, filename=filename, content=content, user_id=user_id, channel=channel)
    key = f"{tenant_id}/replay-{uuid.uuid4().hex}"
    ref = storage.put("data_exports", key, content)
    return _queue(session, tenant_id, mrm_service.REPLAY, f"Replay of {filename[:120]} queued",
                  {"file": filename[:200], "input_ref": ref, "channel": channel}, user_id)


def _execute_replay(session: Session, run: ControlRun) -> None:
    from openclear import storage
    from openclear.services import mrm_service

    ref = run.report["input_ref"]
    try:
        mrm_service.replay(session, run.tenant_id, filename=run.report.get("file", "replay"), content=storage.read(ref),
                           user_id=run.requested_by_user_id, channel=run.report.get("channel", "api"), run=run)
    except mrm_service.ReplayError as exc:
        run.summary, run.report = str(exc)[:500], {**run.report, "state": "failed", "error": str(exc)[:500]}
    finally:
        storage.delete(ref)  # the file was only needed for this run


# --- Evidence export ----------------------------------------------------------------------------

def evidence_runs_inline(start: date, end: date) -> bool:
    return (end - start).days < get_settings().long_request_sync_days


def start_evidence_export(session: Session, tenant_id: uuid.UUID, *, start: date, end: date, fmt: str,
                          user_id: uuid.UUID | None, channel: str) -> ControlRun:
    from openclear.services import evidence_service

    evidence_service._period(start, end)  # refuses a bad period now, not in the job
    return _queue(session, tenant_id, EVIDENCE, f"Evidence export for {start} to {end} ({fmt.upper()}) queued",
                  {"start": start.isoformat(), "end": end.isoformat(), "format": fmt, "channel": channel}, user_id)


def _execute_evidence(session: Session, run: ControlRun) -> None:
    from openclear import storage
    from openclear.bulk_import.signing import sign_file
    from openclear.services import evidence_service

    report = run.report
    start, end, fmt = date.fromisoformat(report["start"]), date.fromisoformat(report["end"]), report["format"]
    signed = evidence_service.sign(evidence_service.build(session, run.tenant_id, start=start, end=end,
                                                          generated_by_user_id=run.requested_by_user_id))
    body = evidence_service.render_pdf(signed) if fmt == "pdf" else json.dumps(signed, indent=2, sort_keys=True, default=str).encode()
    ref = storage.put("data_exports", f"{run.tenant_id}/evidence-{run.id}.{fmt}", body)
    evidence_service.record_export(session, run.tenant_id, user_id=run.requested_by_user_id, channel=report.get("channel", "api"),
                                   start=start, end=end, fmt=fmt, sha256=signed["signature"]["sha256"])
    run.summary = f"Evidence for {start} to {end} ({fmt.upper()}) ready"
    run.report = {**report, "state": "done", "file_ref": ref, "bundle_sha256": signed["signature"]["sha256"],
                  "file_sha256": hashlib.sha256(body).hexdigest(), "file_signature": sign_file(body), "size": len(body)}


def evidence_file(run: ControlRun) -> tuple[bytes, dict]:
    """A finished export's file and the headers to send it with."""
    from openclear import storage

    report = run.report
    name = f"openclear-evidence-{report['start']}-to-{report['end']}.{report['format']}"
    return storage.read(report["file_ref"]), {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-OpenClear-Bundle-SHA256": report["bundle_sha256"], "X-OpenClear-File-SHA256": report["file_sha256"],
        "X-OpenClear-File-Signature": report["file_signature"],
    }


# --- Verifying the action log -------------------------------------------------------------------

def start_verification(session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None) -> ControlRun:
    """Queues a verification, unless one is already waiting or running."""
    waiting = session.execute(select(ControlRun).where(ControlRun.tenant_id == tenant_id, ControlRun.kind == VERIFICATION)
                              .order_by(ControlRun.started_at.desc()).limit(1)).scalar_one_or_none()
    if waiting is not None and state(waiting) in ("queued", "running"):
        return waiting
    return _queue(session, tenant_id, VERIFICATION, "Verifying the action log", {}, user_id)


def verify_now(session: Session, tenant_id: uuid.UUID, run: ControlRun | None = None) -> ControlRun:
    """Verifies the live chain and the archives, and keeps the result as a run."""
    from openclear.services import audit_archive_service, audit_log_service

    chain = audit_log_service.verify_chain(session, tenant_id)
    archives = audit_archive_service.verify_archives(session, tenant_id)
    passed = chain.valid and archives.valid
    report = {"state": "done", "chain_valid": chain.valid, "checked_count": chain.checked_count,
              "broken_at_entry_id": str(chain.broken_at_entry_id) if chain.broken_at_entry_id else None,
              "archives_valid": archives.valid, "archives_checked": archives.checked_archives,
              "archived_entries_checked": archives.checked_entries, "archive_problem": archives.problem}
    summary = (f"Every signature and link verifies ({chain.checked_count} live entries, {archives.checked_archives} archives)"
               if passed else (archives.problem or f"The action log doesn't verify at entry {chain.broken_at_entry_id}"))
    if run is None:
        run = ControlRun(tenant_id=tenant_id, kind=VERIFICATION, requested_by_user_id=None)
        session.add(run)
    run.passed, run.summary, run.report = passed, summary[:500], report
    session.flush()
    return run


def latest_verification(session: Session, tenant_id: uuid.UUID) -> ControlRun | None:
    runs = session.execute(select(ControlRun).where(ControlRun.tenant_id == tenant_id, ControlRun.kind == VERIFICATION)
                           .order_by(ControlRun.started_at.desc()).limit(5)).scalars()
    return next((r for r in runs if state(r) == "done"), None)


def verify_all_banks_job() -> None:
    """Daily, with the other control tests: verify every bank's action log."""
    import logging

    from openclear.db.session import get_session_factory
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.tenant import Tenant

    logger = logging.getLogger(__name__)
    session = get_session_factory()()
    try:
        for tenant_id in list(session.execute(select(Tenant.id).where(Tenant.is_active.is_(True))).scalars()):
            bind_session_to_tenant(session, tenant_id)
            try:
                run = verify_now(session, tenant_id)
                session.commit()
                if not run.passed:
                    logger.error("Action log for tenant %s doesn't verify: %s", tenant_id, run.summary)
            except Exception:  # noqa: BLE001 -- one bank's failure mustn't stop the rest
                session.rollback()
                logger.exception("Couldn't verify the action log for tenant %s", tenant_id)
    finally:
        session.close()


# --- The job ------------------------------------------------------------------------------------

def execute(session: Session, run_id: uuid.UUID) -> None:
    """Runs a queued run (the job handler, workers/job_handlers.py)."""
    from openclear.services import mrm_service

    run = session.get(ControlRun, run_id)
    if run is None or state(run) not in ("queued", "running"):
        return
    run.report = {**run.report, "state": "running", "started": datetime.now(timezone.utc).isoformat()}
    session.flush()
    try:
        if run.kind == mrm_service.REPLAY:
            _execute_replay(session, run)
        elif run.kind == EVIDENCE:
            _execute_evidence(session, run)
        elif run.kind == VERIFICATION:
            verify_now(session, run.tenant_id, run)
        else:
            raise ValueError(f"Not a long run: {run.kind}")
    except Exception as exc:
        session.rollback()
        run = session.get(ControlRun, run_id)
        run.summary = f"Failed: {str(exc)[:400]}"
        run.report = {**(run.report or {}), "state": "failed", "error": str(exc)[:500]}
        session.flush()
        raise


def as_status(run: ControlRun, base_path: str) -> dict:
    report = run.report or {}
    body = {"id": str(run.id), "kind": run.kind, "state": state(run), "summary": run.summary,
            "started_at": run.started_at.isoformat() if run.started_at else None, "passed": run.passed,
            "status_url": f"{base_path}/{run.id}"}
    if run.kind == EVIDENCE and state(run) == "done":
        body["download_url"] = f"{base_path}/{run.id}/file"
    if state(run) == "failed":
        body["error"] = report.get("error")
    if run.kind != EVIDENCE:
        body["report"] = {k: v for k, v in report.items() if k not in ("input_ref", "file_ref")}
    return body
