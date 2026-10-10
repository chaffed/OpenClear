# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Model risk management (COMPLIANCE_PLAN.md C2; services/mrm_service.py): replaying a
historical file through today's rules, and the synthetic anomaly-capture test. Both are
dry runs; only the report is kept."""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.control_run import ControlRun
from openclear.services import mrm_service

router = APIRouter(prefix="/mrm", tags=["model risk"])
_manage = require_permission("admin:manage")
MAX_REPLAY_BYTES = 50 * 1024 * 1024


def _bank_staff(ctx: TenantContext) -> None:
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Model risk tests are for the bank's own staff.")


def run_read(run: ControlRun) -> dict:
    from openclear.services import long_runs

    return {"id": str(run.id), "kind": run.kind, "state": long_runs.state(run),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "passed": run.passed, "summary": run.summary,
            "report": {k: v for k, v in (run.report or {}).items() if k not in ("input_ref", "file_ref")},
            "requested_by_user_id": str(run.requested_by_user_id) if run.requested_by_user_id else None}


@router.post("/replay", status_code=status.HTTP_201_CREATED, responses={202: {
    "description": "A large file: the replay runs in the background. Poll `GET /api/v1/mrm/runs/{id}` until `state` is done."}})
def replay(file: UploadFile = File(..., description="A historical X9.37 cash letter or NACHA file"),
           db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> dict:
    """Runs the file through today's rules in a dry run (nothing is kept but the report) and
    compares each item with what happened to it then: `match_rate` (today's rules flag it
    exactly when they did then), `false_positive_rate` (flagged, but the bank paid it),
    `missed_return_rate` (not flagged, but the bank returned it), `unhandled_rate`, and the
    items that differ. A file over `long_request_sync_bytes` (1 MB) is replayed in the
    background: the answer is `202` with the run, `state` queued; poll the run."""
    _bank_staff(ctx)
    content = file.file.read(MAX_REPLAY_BYTES + 1)
    if len(content) > MAX_REPLAY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"At most {MAX_REPLAY_BYTES // (1024 * 1024)} MB per replay.")
    from fastapi.responses import JSONResponse

    from openclear.services import long_runs
    from openclear.workers.jobs import jobs_queued

    try:
        run = long_runs.start_replay(db, ctx.tenant_id, filename=file.filename or "replay", content=content, user_id=ctx.user_id,
                                     channel="api")
    except mrm_service.ReplayError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    if long_runs.state(run) == "done":
        return run_read(run)
    jobs_queued(db.get_bind())
    db.refresh(run)
    return JSONResponse(run_read(run), status_code=status.HTTP_202_ACCEPTED if long_runs.state(run) != "done" else status.HTTP_201_CREATED)


@router.post("/anomaly-capture", status_code=status.HTTP_201_CREATED)
def anomaly_capture(db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> dict:
    """Runs the synthetic anomaly-capture test now, against this bank's live rules (dry run).
    `passed` only if every injected anomaly was caught and the clean items weren't flagged."""
    _bank_staff(ctx)
    run = mrm_service.anomaly_capture(db, ctx.tenant_id, user_id=ctx.user_id)
    db.commit()
    return run_read(run)


@router.get("/runs")
def list_runs(kind: str | None = None, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> list[dict]:
    """The latest replays and anomaly-capture runs (`kind`: `mrm_replay` or `anomaly_capture`)."""
    _bank_staff(ctx)
    return [run_read(r) for r in mrm_service.recent_runs(db, ctx.tenant_id, kind)]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> dict:
    _bank_staff(ctx)
    run = db.get(ControlRun, run_id)
    if run is None or run.tenant_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run_read(run)
