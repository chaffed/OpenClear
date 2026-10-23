# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform pages (UX_PLAN.md U8, U9): platform staff sign in here, separately from any
bank. An operator sees every bank (services/operator_service.py) and sets up new ones. A
support person sees the access banks have granted them, and opens a bank only through one
(services/support_access_service.py)."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth import login_service
from openclear.db.session import get_db
from openclear.domain.user import User
from openclear.services import operator_service, support_access_service
from openclear.web import platform_session
from openclear.web.deps import render_template
from openclear.web.rate_limit import rate_limit
from openclear.web.redirects import see_other
from openclear.web.security import clear_auth_cookies, verify_csrf, verify_csrf_header

router = APIRouter(prefix="/ui/platform", tags=["web-platform"])


@router.get("/login")
def login_form(request: Request) -> HTMLResponse:
    return render_template(request, "platform/login.html")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db),
          _csrf: None = Depends(verify_csrf),
          _limit: None = Depends(rate_limit("login", limit_setting="login_attempts_per_minute"))) -> HTMLResponse:
    user = login_service.authenticate_platform(db, email, password)
    db.commit()
    if user is None:
        return render_template(request, "platform/login.html", error="Invalid email or password.", email=email, status_code=401)
    # A security key always follows the password (OW-D23): registered here the first time.
    from openclear.auth import platform_webauthn_service

    step = "key" if platform_webauthn_service.credentials(db, user.id) else "key/setup"
    response = see_other(f"/ui/platform/login/{step}")
    platform_session.set_pending_cookie(response, platform_session.create_pending_token(user))
    return response


def _key_page(request: Request, db: Session, template: str) -> HTMLResponse:
    if platform_session.pending_user(request, db) is None:
        return see_other("/ui/platform/login")
    return render_template(request, template)


@router.get("/login/key")
def key_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _key_page(request, db, "platform/login_key.html")


@router.get("/login/key/setup")
def key_setup_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _key_page(request, db, "platform/login_key_setup.html")


def _pending_or_401(request: Request, db: Session):
    user = platform_session.pending_user(request, db)
    if user is None:
        raise _KeyError("Your sign-in expired: start again.")
    return user


class _KeyError(Exception):
    pass


def _signed_in(user) -> JSONResponse:
    response = JSONResponse({"redirect": "/ui/platform"})
    platform_session.clear_pending_cookie(response)
    platform_session.set_cookie(response, platform_session.create_token(user))
    return response


@router.post("/login/key/options")
def key_options(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf_header)) -> Response:
    from openclear.auth import platform_webauthn_service
    from openclear.auth.webauthn_service import WebauthnError

    try:
        options = platform_webauthn_service.begin_authentication(db, _pending_or_401(request, db))
    except (_KeyError, WebauthnError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    db.commit()
    return Response(content=options, media_type="application/json")


@router.post("/login/key/verify")
async def key_verify(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf_header)) -> Response:
    from openclear.auth import platform_webauthn_service
    from openclear.auth.webauthn_service import WebauthnError

    body = await request.json()
    try:
        user = _pending_or_401(request, db)
        platform_webauthn_service.complete_authentication(db, user, body.get("credential"))
    except (_KeyError, WebauthnError) as exc:
        db.commit()
        return JSONResponse({"error": str(exc)}, status_code=400)
    db.commit()
    return _signed_in(user)


@router.post("/login/key/setup/options")
def key_setup_options(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf_header)) -> Response:
    from openclear.auth import platform_webauthn_service

    try:
        user = _pending_or_401(request, db)
    except _KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if platform_webauthn_service.credentials(db, user.id):  # a first key only: later ones need the existing one
        return JSONResponse({"error": "You already have a security key: sign in with it."}, status_code=400)
    options = platform_webauthn_service.begin_registration(db, user)
    db.commit()
    return Response(content=options, media_type="application/json")


@router.post("/login/key/setup/verify")
async def key_setup_verify(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf_header)) -> Response:
    from openclear.auth import platform_webauthn_service
    from openclear.auth.webauthn_service import WebauthnError

    body = await request.json()
    try:
        user = _pending_or_401(request, db)
        if platform_webauthn_service.credentials(db, user.id):
            raise _KeyError("You already have a security key: sign in with it.")
        row = platform_webauthn_service.complete_registration(db, user, body.get("credential"), nickname=body.get("nickname"))
    except (_KeyError, WebauthnError) as exc:
        db.commit()
        return JSONResponse({"error": str(exc)}, status_code=400)
    from openclear.services import platform_log_service

    platform_log_service.record(db, action="platform.security_key.added", summary=f"{user.email} added a security key"
                                f"{' (' + row.nickname + ')' if row.nickname else ''}", actor_user_id=user.id)
    db.commit()
    return _signed_in(user)


@router.post("/logout")
def logout(_csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    response = see_other("/ui/platform/login")
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


@router.get("/banks/{tenant_id}/reset-admin-key")
def reset_admin_key_form(request: Request, tenant_id: uuid.UUID, db: Session = Depends(get_db),
                         _user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    bank = operator_service.tenant_by_id(db, tenant_id)
    if bank is None:
        return see_other("/ui/platform")
    return render_template(request, "platform/reset_admin_key.html", bank=bank)


@router.post("/banks/{tenant_id}/reset-admin-key")
def reset_admin_key(request: Request, tenant_id: uuid.UUID, email: str = Form(...), identity_check: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                    _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    """A bank whose only administrator lost their key can't reset them itself: platform staff can,
    as a change a second operator approves (OW-D21; ADR 0004)."""
    from openclear.services import change_request_service
    from openclear.services.change_kinds import sole_bank_admin

    bank = operator_service.tenant_by_id(db, tenant_id)
    if bank is None:
        return see_other("/ui/platform")
    error = None
    if len(identity_check.strip()) < 10:
        error = "Say how you confirmed it's really them, for example a call to a number the bank gave you."
    elif sole_bank_admin(db, tenant_id, email) is None:
        error = "Only the bank's only administrator can be reset from here; otherwise another administrator at the bank does it."
    if error:
        return render_template(request, "platform/reset_admin_key.html", bank=bank, email=email, identity_check=identity_check,
                               error=error, status_code=422)
    change_request_service.request_or_apply(
        db, "platform.security_key_reset", {"bank_id": str(tenant_id), "email": email.strip().lower(),
                                            "identity_check": identity_check.strip()[:300]},
        tenant_id=None, user_id=user.id, summary=f"Reset the security keys of {email.strip()} at {bank.name}. "
                                                 f"Identity confirmed by: {identity_check.strip()[:300]}")
    db.commit()
    return see_other("/ui/platform/changes", flash="Another operator resets them by approving it under Changes.")


def _offboarding_page(request: Request, db: Session, bank, *, error: str | None = None, status_code: int = 200, **form) -> HTMLResponse:
    from openclear.services import offboarding_service

    preview = None
    if bank.data_deleted_at is None and not bank.is_demo and not bank.is_sandbox:
        preview = offboarding_service.preview(db, bank.id)
    return render_template(request, "platform/offboarding.html", bank=bank, preview=preview, error=error, form=form,
                           deletable_from=offboarding_service.deletable_from(bank), wait_days=offboarding_service.wait_days(),
                           delay_hours=offboarding_service.delay_hours(), deletion=offboarding_service.latest(db, bank.id),
                           status_code=status_code)


@router.get("/banks/{tenant_id}/offboarding")
def offboarding_form(request: Request, tenant_id: uuid.UUID, db: Session = Depends(get_db),
                     _user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    """Offboarding a bank: telling it, the wait, its legal hold, and deleting its data with a
    signed certificate (plans/OFFBOARDING_PLAN.md). Each step is a change under Changes."""
    bank = operator_service.tenant_by_id(db, tenant_id)
    if bank is None:
        return see_other("/ui/platform")
    return _offboarding_page(request, db, bank)


@router.post("/banks/{tenant_id}/offboarding")
def offboarding(request: Request, tenant_id: uuid.UUID, step: str = Form(...), reason: str = Form(""), confirm_slug: str = Form(""),
                override_reason: str = Form(""), db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    from openclear.services import change_request_service, offboarding_service

    bank = operator_service.tenant_by_id(db, tenant_id)
    if bank is None:
        return see_other("/ui/platform")
    bank_id = str(bank.id)
    if step == "start":
        kind, payload, summary = "platform.bank.offboard", {"bank_id": bank_id, "offboarding": True}, f"Start offboarding {bank.name}"
    elif step == "stop":
        kind, payload, summary = "platform.bank.offboard", {"bank_id": bank_id, "offboarding": False}, f"Stop offboarding {bank.name}"
    elif step == "hold":
        kind, payload, summary = "platform.bank.hold", {"bank_id": bank_id, "held": True, "reason": reason.strip()[:500]}, \
            f"Put {bank.name}'s data on legal hold: {reason.strip()[:300]}"
    elif step == "release":
        kind, payload, summary = "platform.bank.hold", {"bank_id": bank_id, "held": False}, f"Take {bank.name}'s data off legal hold"
    elif step == "delete":
        preview = offboarding_service.preview(db, bank.id)
        kind = "platform.bank.delete"
        payload = {"bank_id": bank_id, "confirm_slug": confirm_slug.strip().lower(), "override_reason": override_reason.strip()[:500] or None,
                   "requested_by": str(user.id), "rows": preview.total, "tables": len(preview.rows),
                   "people_deleted": len(preview.people_deleted), "people_kept": len(preview.people_kept)}
        summary = (f"Delete {bank.name}'s data: {preview.total} rows in {len(preview.rows)} tables, "
                   f"{len(preview.people_deleted)} people ({len(preview.people_kept)} kept, at other banks); "
                   + (f"final export {preview.final_export_at:%Y-%m-%d}" if preview.final_export_at
                      else f"no final export, because: {override_reason.strip()[:200]}"))
    else:
        return see_other(f"/ui/platform/banks/{bank_id}/offboarding")
    try:
        outcome = change_request_service.request_or_apply(db, kind, payload, tenant_id=None, user_id=user.id, summary=summary)
    except change_request_service.ChangeRequestError as exc:
        db.rollback()
        return _offboarding_page(request, db, bank, error=str(exc), status_code=422, reason=reason, confirm_slug=confirm_slug,
                                 override_reason=override_reason)
    db.commit()
    if outcome.applied:
        return see_other(f"/ui/platform/banks/{bank_id}/offboarding", flash=outcome.summary)
    return see_other("/ui/platform/changes", flash="Another operator makes it by approving it under Changes.")


@router.get("/banks/{tenant_id}/deletion-certificate")
def deletion_certificate(tenant_id: uuid.UUID, db: Session = Depends(get_db),
                         _user: User = Depends(platform_session.require_operator)) -> Response:
    """The signed certificate of a bank's deletion, for the operator to send to the bank."""
    import json

    from openclear.services import offboarding_service

    record = offboarding_service.latest(db, tenant_id)
    if record is None or record.certificate is None:
        return see_other(f"/ui/platform/banks/{tenant_id}/offboarding")
    slug = record.certificate["bundle"]["bank"]["slug"]
    return Response(json.dumps(record.certificate, indent=2, sort_keys=True), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="openclear-deletion-{slug}.json"'})


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

    from openclear.config import get_settings
    from openclear.services import attestation_service

    if get_settings().host_cuecs_attested_by != "platform":
        return see_other("/ui/platform/host-attestations", flash="Each bank attests the host's CUECs on this installation.")
    from openclear.services import platform_log_service

    try:
        made = attestation_service.record_installation(db, cuec_id, start=date.fromisoformat(start), end=date.fromisoformat(end), result=result,
                                                       statement=statement, user_id=user.id)
    except (attestation_service.AttestationError, ValueError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, attestation_service.AttestationError) else "Choose the period's dates."
        return see_other("/ui/platform/host-attestations", flash=message)
    platform_log_service.record(db, actor_user_id=user.id, action="platform.host_attestation.prepare",
                                summary=f"Prepared {cuec_id} for {made.period_start} to {made.period_end}: {made.result}",
                                resource_type="cuec_attestation", resource_id=made.id)
    db.commit()
    return see_other("/ui/platform/host-attestations", flash=f"{cuec_id} prepared: someone else accepts and signs it.")


@router.post("/host-attestations/{attestation_id}/accept")
def accept_host_attestation(attestation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                            _csrf: None = Depends(verify_csrf)) -> RedirectResponse:

    from openclear.domain.cuec_attestation import CuecAttestation
    from openclear.services import attestation_service, platform_log_service

    attestation = db.get(CuecAttestation, attestation_id)
    if attestation is None or attestation.tenant_id is not None:
        return see_other("/ui/platform/host-attestations", flash="No such host attestation.")
    try:
        attestation_service.accept_installation(db, attestation, user_id=user.id)
    except attestation_service.AttestationError as exc:
        db.rollback()
        return see_other("/ui/platform/host-attestations", flash=str(exc))
    platform_log_service.record(db, actor_user_id=user.id, action="platform.host_attestation.accept",
                                summary=f"Accepted and signed {attestation.cuec_id} for {attestation.period_start} to {attestation.period_end}",
                                resource_type="cuec_attestation", resource_id=attestation.id)
    db.commit()
    return see_other("/ui/platform/host-attestations", flash=f"{attestation.cuec_id} accepted and signed.")


@router.get("/incidents")
def incidents(request: Request, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    """Incident and maintenance notices to banks (services/incident_service.py; REGULATORY_PLAN.md RG3)."""
    from openclear.services import incident_service

    return render_template(request, "platform/incidents.html", user=user, banks=operator_service.banks(db, include_sandboxes=True),
                           notices=incident_service.issued(db), kinds=incident_service.KINDS)


@router.post("/incidents")
async def issue_incident_notice(request: Request, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                                _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Issues a notice at once to the banks chosen: speed comes first (12 CFR 304.24 says as soon as
    possible), so it doesn't wait for a second operator; the platform's action log records it."""
    from datetime import datetime, timezone

    from openclear.services import incident_service

    form = await request.form()

    def when(name: str):
        value = str(form.get(name) or "").strip()
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None

    try:
        chosen = [uuid.UUID(v) for v in form.getlist("tenant_ids")]
        if form.get("all_banks") == "true":
            chosen = [b.tenant.id for b in operator_service.banks(db, include_sandboxes=True)]
        follows = uuid.UUID(str(form["follows_id"])) if form.get("follows_id") else None
        notice = incident_service.issue(
            db, kind=str(form.get("kind") or ""), title=str(form.get("title") or ""), message=str(form.get("message") or ""),
            tenant_ids=chosen, user_id=user.id, started_at=when("started_at"), determined_at=when("determined_at"),
            services_affected=str(form.get("services_affected") or ""), bank_data_affected=form.get("bank_data_affected") == "true",
            follows_id=follows)
    except (incident_service.IncidentError, ValueError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, incident_service.IncidentError) else "Check the dates and the banks chosen."
        return see_other("/ui/platform/incidents", flash=message)
    db.commit()
    return see_other("/ui/platform/incidents", flash=f"Notice {notice.title!r} sent.")


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
    return see_other("/ui/platform/changes", flash=note)


@router.get("/changes")
def changes_page(request: Request, db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator)) -> HTMLResponse:
    """The service provider's changes waiting for a second operator, and its action log."""
    from openclear.services import change_request_service, platform_log_service

    waiting = change_request_service.pending(db, None)
    rows = [{"change": c, "mine": c.requested_by_user_id == user.id, "can_decide": change_request_service.can_decide(db, c, user.id)}
            for c in waiting]
    from openclear.services import installation_settings

    run = platform_log_service.verify_and_keep(db)  # kept for every bank's "Service provider's action log intact" check
    intact, checked, broken = run.intact, run.checked, run.broken_at_sequence
    changed_settings, _row = installation_settings.changed(db)
    db.commit()  # the verification, and the first baseline if this was the first look
    return render_template(request, "platform/changes.html", user=user, rows=rows, log=platform_log_service.entries(db, limit=50),
                           intact=intact, checked=checked, broken=broken, changed_settings=changed_settings)


@router.post("/settings/acknowledge")
def acknowledge_settings(db: Session = Depends(get_db), user: User = Depends(platform_session.require_operator),
                         _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """The installation's settings are as intended (DUAL_CONTROL_PLAN.md DC-D14): in the provider's log."""
    from openclear.services import installation_settings

    names = installation_settings.acknowledge(db, user_id=user.id)
    db.commit()
    return see_other("/ui/platform/changes", flash='Acknowledged: ' + (', '.join(names) or 'no change'))


@router.post("/changes/{change_id}/{verdict}")
def decide_change(request: Request, change_id: uuid.UUID, verdict: str, reason: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(platform_session.require_operator), _csrf: None = Depends(verify_csrf)) -> Response:
    from openclear.services import change_request_service

    if verdict not in ("approve", "reject", "withdraw"):
        return see_other("/ui/platform/changes")
    try:
        if verdict == "withdraw":
            change_request_service.withdraw(db, None, change_id, user_id=user.id)
        else:
            change_request_service.decide(db, None, change_id, approve=verdict == "approve", user_id=user.id, reason=reason)
    except (change_request_service.ChangeRequestError, ValueError) as exc:
        db.rollback()
        return see_other("/ui/platform/changes", flash=str(exc))
    provisioned = db.info.pop("provisioned", None)
    db.commit()
    if provisioned:
        tenant, password, admin_email = provisioned
        return render_template(request, "platform/provisioned.html", tenant=tenant, admin_email=admin_email, password=password)
    done = {"approve": "Approved: it has taken effect.", "reject": "Rejected.", "withdraw": "Withdrawn."}[verdict]
    return see_other("/ui/platform/changes", flash=done)


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
        return see_other("/ui/platform", error=str(exc))
    db.commit()
    expires_at = support_access_service._aware(grant.expires_at)
    token = create_token(user_id=user.id, tenant_id=grant.tenant_id, security_group_id=uuid.UUID(int=0), token_type="access",
                         token_version=user.token_version, expires_at=expires_at, support_grant_id=grant.id)
    response = see_other("/ui/")
    platform_session.set_support_session(response, token, expires_at)
    return response


@router.post("/support/end")
def end_support(_csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Leaves the bank: the support session's cookie goes; the grant stays as it is."""
    response = see_other("/ui/platform")
    clear_auth_cookies(response)
    return response
