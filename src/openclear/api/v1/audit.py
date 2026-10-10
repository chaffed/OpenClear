# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The evidence export (COMPLIANCE_PLAN.md C3; services/evidence_service.py)."""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from openclear.api.v1.files import file_response
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import evidence_service

router = APIRouter(prefix="/audit", tags=["audit"])


def export_response(db: Session, ctx: TenantContext, start: date, end: date, fmt: str, channel: str) -> Response:
    """The signed export, as JSON or PDF; shared with the web page."""
    import hashlib
    import json

    from openclear.bulk_import.signing import sign_file

    try:
        signed = evidence_service.sign(evidence_service.build(db, ctx.tenant_id, start=start, end=end, generated_by_user_id=ctx.user_id))
    except evidence_service.EvidenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    name = f"openclear-evidence-{start.isoformat()}-to-{end.isoformat()}"
    if fmt == "pdf":
        from openclear.services.doc_pdf_service import weasyprint_usable

        if not weasyprint_usable():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF rendering isn't available on this server; export JSON.")
        body, media_type = evidence_service.render_pdf(signed), "application/pdf"
    else:
        body, media_type = json.dumps(signed, indent=2, sort_keys=True, default=str).encode(), "application/json"
    evidence_service.record_export(db, ctx.tenant_id, user_id=ctx.user_id, channel=channel, start=start, end=end, fmt=fmt,
                                   sha256=signed["signature"]["sha256"])
    db.commit()
    return Response(body, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{name}.{fmt}"',
        "X-OpenClear-Bundle-SHA256": signed["signature"]["sha256"],
        # The file itself, as sent: ECDSA P-256 over its bytes with the file-signing key.
        "X-OpenClear-File-SHA256": hashlib.sha256(body).hexdigest(),
        "X-OpenClear-File-Signature": sign_file(body),
    })


_EXPORT = file_response("application/octet-stream", "The signed evidence: JSON (application/json) or PDF (application/pdf)")
# A 202 (a long period, run in the background) is JSON with `status_url`; declared without a
# body type so generated SDKs still treat the answer as a file (the CLI looks for the JSON).
_EXPORT["responses"][202] = {"description": "A long period: the export runs in the background. The body is JSON with `status_url`; "
                                            "poll it, then download from `download_url`."}


@router.get("/export", **_EXPORT)
def export(start: date = Query(..., description="First day, YYYY-MM-DD (UTC)"), end: date = Query(..., description="Last day, inclusive"),
           format: Literal["json", "pdf"] = "json", db: Session = Depends(get_db),
           ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> Response:
    """A signed evidence bundle for the period, for an examiner or auditor: the system version;
    the rules, plugins and models in force with their digests; settings and security groups;
    whether the action log verifies; segregation-of-duties breaches; changes and approvals;
    every decision with who recommended and decided it; model-risk test runs; and the live
    control status. JSON carries `bundle` and its `signature` (ECDSA P-256 over the SHA-256
    of the bundle as compact, key-sorted JSON) with the public key; a PDF shows the same, and
    both carry the file's own signature in `X-OpenClear-File-Signature`. Each export is in
    the action log. A period longer than `long_request_sync_days` (32) is exported in the
    background: the answer is `202` with `status_url`, and `download_url` once it's done."""
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Evidence exports are for the bank's own staff.")
    from openclear.services import long_runs

    if long_runs.evidence_runs_inline(start, end):
        return export_response(db, ctx, start, end, format, "api")
    return _queued_export(db, ctx, start, end, format, "api")


def _queued_export(db: Session, ctx: TenantContext, start: date, end: date, fmt: str, channel: str):
    from fastapi.responses import JSONResponse

    from openclear.services import evidence_service, long_runs
    from openclear.workers.jobs import jobs_queued

    try:
        run = long_runs.start_evidence_export(db, ctx.tenant_id, start=start, end=end, fmt=fmt, user_id=ctx.user_id, channel=channel)
    except evidence_service.EvidenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    jobs_queued(db.get_bind())
    db.refresh(run)
    return JSONResponse(long_runs.as_status(run, "/api/v1/audit/export/runs"), status_code=status.HTTP_202_ACCEPTED)


def _export_run(db: Session, ctx: TenantContext, run_id):
    from openclear.services import long_runs

    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Evidence exports are for the bank's own staff.")
    run = long_runs.get_run(db, ctx.tenant_id, run_id, long_runs.EVIDENCE)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    return run


@router.get("/export/runs/{run_id}")
def export_status(run_id: uuid.UUID, db: Session = Depends(get_db),
                  ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> dict:
    """A long export's progress: `state` is queued, running, done or failed; when done,
    `download_url` gives the signed file."""
    from openclear.services import long_runs

    return long_runs.as_status(_export_run(db, ctx, run_id), "/api/v1/audit/export/runs")


@router.get("/export/runs/{run_id}/file", **file_response("application/octet-stream", "The signed evidence: JSON or PDF"))
def export_file(run_id: uuid.UUID, db: Session = Depends(get_db),
                ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> Response:
    from openclear.services import long_runs

    run = _export_run(db, ctx, run_id)
    if long_runs.state(run) != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "The export isn't ready yet.")
    body, headers = long_runs.evidence_file(run)
    return Response(body, media_type="application/pdf" if run.report["format"] == "pdf" else "application/json", headers=headers)
