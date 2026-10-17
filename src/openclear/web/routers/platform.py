# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform pages (UX_PLAN.md U8, U9): platform staff sign in here, separately from any
bank. An operator sees every bank (services/operator_service.py) and sets up new ones. A
support person sees the access banks have granted them, and opens a bank only through one
(services/support_access_service.py)."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth import login_service
from openclear.db.session import get_db
from openclear.domain.user import User
from openclear.services import operator_service, support_access_service
from openclear.web import platform_session
from openclear.web.deps import render_template
from openclear.web.security import clear_auth_cookies, verify_csrf

router = APIRouter(prefix="/ui/platform", tags=["web-platform"])


@router.get("/login")
def login_form(request: Request) -> HTMLResponse:
    return render_template(request, "platform/login.html")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db),
          _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    user = login_service.authenticate_platform(db, email, password)
    db.commit()
    if user is None:
        return render_template(request, "platform/login.html", error="Invalid email or password.", email=email, status_code=401)
    response = RedirectResponse("/ui/platform", status_code=303)
    platform_session.set_cookie(response, platform_session.create_token(user))
    return response


@router.post("/logout")
def logout(_csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    response = RedirectResponse("/ui/platform/login", status_code=303)
    platform_session.clear_cookie(response)
    return response


@router.get("")
def home(request: Request, sandboxes: bool = False, db: Session = Depends(get_db),
         user: User = Depends(platform_session.current_user)) -> HTMLResponse:
    grants = support_access_service.for_support_person(db, user.id)
    db.commit()  # any grants that expired just now are recorded
    rows = operator_service.banks(db, include_sandboxes=sandboxes) if user.platform_role == "operator" else []
    return render_template(request, "platform/home.html", user=user, grants=grants, banks=rows, sandboxes=sandboxes)


@router.get("/banks/{tenant_id}/controls")
def bank_controls(tenant_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(platform_session.require_operator)) -> JSONResponse:
    """A bank's controls summary, fetched after the page loads (it takes a moment)."""
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.services import controls_service

    if operator_service.tenant_by_id(db, tenant_id) is None:
        return JSONResponse({"state": "—", "tone": ""}, status_code=404)
    bind_session_to_tenant(db, tenant_id)
    counts = controls_service.summary(controls_service.status(db, tenant_id))["counts"]
    if counts.get("FAIL"):
        return JSONResponse({"state": f"{counts['FAIL']} FAIL", "tone": "danger"})
    if counts.get("WARN"):
        return JSONResponse({"state": f"{counts['WARN']} WARN", "tone": "warn"})
    return JSONResponse({"state": "All pass", "tone": "ok"})


@router.get("/host-attestations")
def host_attestations(request: Request, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    """The host's CUECs, attested once for the installation (CONTROLS_PLAN.md CT-D12), when
    OPENCLEAR_HOST_CUECS_ATTESTED_BY is "platform"."""
    from datetime import date, timedelta

    from openclear.compliance import cuecs
    from openclear.config import get_settings
    from openclear.services import attestation_service

    rows = attestation_service.installation_attestations(db)
    waiting = [a for a in rows if a.status == "prepared"]
    latest = {}
    for a in rows:
        if a.status == "accepted" and a.cuec_id not in latest:
            latest[a.cuec_id] = a
    today = date.today()
    return render_template(request, "platform/host_attestations.html", user=user, cuecs=cuecs.for_party("host"), latest=latest, waiting=waiting,
                           results=attestation_service.MANUAL_RESULTS, by_platform=get_settings().host_cuecs_attested_by == "platform",
                           default_start=(today.replace(day=1) - timedelta(days=1)).replace(day=1), default_end=today.replace(day=1) - timedelta(days=1))


@router.post("/host-attestations")
def record_host_attestation(cuec_id: str = Form(...), start: str = Form(...), end: str = Form(...), result: str = Form(...),
                            statement: str = Form(""), db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                            _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    from datetime import date
    from urllib.parse import quote

    from openclear.config import get_settings
    from openclear.services import attestation_service

    if get_settings().host_cuecs_attested_by != "platform":
        return RedirectResponse("/ui/platform/host-attestations?flash=" + quote("Each bank attests the host's CUECs on this installation."), status_code=303)
    from openclear.services import platform_log_service

    try:
        made = attestation_service.record_installation(db, cuec_id, start=date.fromisoformat(start), end=date.fromisoformat(end), result=result,
                                                       statement=statement, user_id=user.id)
    except (attestation_service.AttestationError, ValueError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, attestation_service.AttestationError) else "Choose the period's dates."
        return RedirectResponse("/ui/platform/host-attestations?flash=" + quote(message), status_code=303)
    platform_log_service.record(db, actor_user_id=user.id, action="platform.host_attestation.prepare",
                                summary=f"Prepared {cuec_id} for {made.period_start} to {made.period_end}: {made.result}",
                                resource_type="cuec_attestation", resource_id=made.id)
    db.commit()
    return RedirectResponse("/ui/platform/host-attestations?flash=" + quote(f"{cuec_id} prepared: someone else accepts and signs it."),
                            status_code=303)


@router.post("/host-attestations/{attestation_id}/accept")
def accept_host_attestation(attestation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                            _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    from urllib.parse import quote

    from openclear.domain.cuec_attestation import CuecAttestation
    from openclear.services import attestation_service, platform_log_service

    attestation = db.get(CuecAttestation, attestation_id)
    if attestation is None or attestation.tenant_id is not None:
        return RedirectResponse("/ui/platform/host-attestations?flash=" + quote("No such host attestation."), status_code=303)
    try:
        attestation_service.accept_installation(db, attestation, user_id=user.id)
    except attestation_service.AttestationError as exc:
        db.rollback()
        return RedirectResponse("/ui/platform/host-attestations?flash=" + quote(str(exc)), status_code=303)
    platform_log_service.record(db, actor_user_id=user.id, action="platform.host_attestation.accept",
                                summary=f"Accepted and signed {attestation.cuec_id} for {attestation.period_start} to {attestation.period_end}",
                                resource_type="cuec_attestation", resource_id=attestation.id)
    db.commit()
    return RedirectResponse("/ui/platform/host-attestations?flash=" + quote(f"{attestation.cuec_id} accepted and signed."), status_code=303)


@router.get("/banks/new")
def new_bank(request: Request, _user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    return render_template(request, "platform/provision.html")


@router.post("/banks")
def create_bank(request: Request, name: str = Form(...), slug: str = Form(...), admin_email: str = Form(...),
                db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    """Asks for a new bank: it's set up when another operator approves it (DUAL_CONTROL_PLAN.md DC4),
    who then sees the first admin's temporary password to pass on."""
    from openclear.services import change_request_service

    savepoint = db.begin_nested()  # checked now, so a mistake shows at once rather than on approval
    try:
        operator_service.provision(db, name=name, slug=slug, admin_email=admin_email, operator_email=user.email)
    except operator_service.ProvisioningError as exc:
        savepoint.rollback()
        return render_template(request, "platform/provision.html", error=str(exc), bank_name=name, slug=slug, admin_email=admin_email,
                               status_code=422)
    savepoint.rollback()
    try:
        outcome = change_request_service.request_or_apply(
            db, "platform.provision", {"name": name.strip(), "slug": slug.strip().lower(), "admin_email": admin_email.strip().lower(),
                                       "operator_email": user.email}, tenant_id=None, user_id=user.id, summary=f"Set up {name.strip()} ({slug.strip().lower()})")
    except change_request_service.ChangeRequestError as exc:
        db.rollback()
        return render_template(request, "platform/provision.html", error=str(exc), bank_name=name, slug=slug, admin_email=admin_email,
                               status_code=422)
    db.commit()
    note = ("Nobody else can approve it, so it's set up in 24 hours unless you withdraw it." if outcome.change and outcome.change.applies_at
            else "Another operator sets it up by approving it under Changes.")
    return RedirectResponse(f"/ui/platform/changes?flash={quote(note)}", status_code=303)


@router.get("/changes")
def changes_page(request: Request, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    """The service provider's changes waiting for a second operator, and its action log."""
    from openclear.services import change_request_service, platform_log_service

    waiting = change_request_service.pending(db, None)
    rows = [{"change": c, "mine": c.requested_by_user_id == user.id, "can_decide": change_request_service.can_decide(db, c, user.id)}
            for c in waiting]
    from openclear.services import installation_settings

    intact, checked, broken = platform_log_service.verify(db)
    changed_settings, _row = installation_settings.changed(db)
    db.commit()  # the first baseline, if this was the first look
    return render_template(request, "platform/changes.html", user=user, rows=rows, log=platform_log_service.entries(db, limit=50),
                           intact=intact, checked=checked, broken=broken, changed_settings=changed_settings)


@router.post("/settings/acknowledge")
def acknowledge_settings(db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                         _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """The installation's settings are as intended (DUAL_CONTROL_PLAN.md DC-D14): in the provider's log."""
    from openclear.services import installation_settings

    names = installation_settings.acknowledge(db, user_id=user.id)
    db.commit()
    return RedirectResponse(f"/ui/platform/changes?flash={quote('Acknowledged: ' + (', '.join(names) or 'no change'))}", status_code=303)


@router.post("/changes/{change_id}/{verdict}")
def decide_change(request: Request, change_id: uuid.UUID, verdict: str, reason: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(platform_session.require_operator), _csrf: None = Depends(verify_csrf)) -> Response:
    from openclear.services import change_request_service

    if verdict not in ("approve", "reject", "withdraw"):
        return RedirectResponse("/ui/platform/changes", status_code=303)
    try:
        if verdict == "withdraw":
            change_request_service.withdraw(db, None, change_id, user_id=user.id)
        else:
            change_request_service.decide(db, None, change_id, approve=verdict == "approve", user_id=user.id, reason=reason)
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return RedirectResponse(f"/ui/platform/changes?flash={quote(str(exc))}", status_code=303)
    provisioned = db.info.pop("provisioned", None)
    db.commit()
    if provisioned:
        tenant, password, admin_email = provisioned
        return render_template(request, "platform/provisioned.html", tenant=tenant, admin_email=admin_email, password=password)
    done = {"approve": "Approved: it has taken effect.", "reject": "Rejected.", "withdraw": "Withdrawn."}[verdict]
    return RedirectResponse(f"/ui/platform/changes?flash={quote(done)}", status_code=303)


@router.post("/support/{grant_id}/start")
def start_support(grant_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(platform_session.current_user),
                  _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Opens the bank under a grant it made to this person: a session built from the grant,
    under their own name, until the grant ends."""
    from openclear.auth.security import create_token

    try:
        grant = support_access_service.start(db, grant_id, user=user)
    except support_access_service.SupportAccessError as exc:
        db.commit()
        return RedirectResponse(f"/ui/platform?error={quote(str(exc))}", status_code=303)
    db.commit()
    expires_at = support_access_service._aware(grant.expires_at)
    token = create_token(user_id=user.id, tenant_id=grant.tenant_id, security_group_id=uuid.UUID(int=0), token_type="access",
                         token_version=user.token_version, expires_at=expires_at, support_grant_id=grant.id)
    response = RedirectResponse("/ui/", status_code=303)
    platform_session.set_support_session(response, token, expires_at)
    return response


@router.post("/support/end")
def end_support(_csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Leaves the bank: the support session's cookie goes; the grant stays as it is."""
    response = RedirectResponse("/ui/platform", status_code=303)
    clear_auth_cookies(response)
    return response
