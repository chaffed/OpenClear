# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OSCAL 1.2.1 documents for GRC tools (CONTROLS_PLAN.md CT5), checked against NIST's published
JSON schemas in the test suite (tests/fixtures/oscal/).

- **Component definition**: OpenClear as a software component, implementing NIST SP 800-53 Rev. 5.
  One implemented requirement per 800-53 control either inventory is filed under. OSCAL's
  component definition has no field for a customer's responsibility, so each OpenClear control
  and each CUEC on a control is a property in OpenClear's namespace (`NS`): `openclear-control`,
  or `cuec` with the party as its class, each with its statement as remarks; and each NIST CSF
  2.0 subcategory NIST maps to the control is a `csf-subcategory` property (CT6). The parties
  are also its responsible roles.
- **Assessment results**: a bank's period. One observation per check that ran in it (its
  results' counts and the digest over them), one finding per deviation in it (not satisfied,
  against the objective of the check's control), and each accepted attestation as an
  attestation. A bank's checks run on their own schedule rather than from an assessment plan,
  so `import-ap` points at a back-matter resource that says so.

UUIDs not taken from a row are version 5, from `NS`, so the same thing has the same UUID in
every document."""

import hashlib
import uuid
from datetime import date, datetime, time, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.compliance import controls, cuecs
from openclear.compliance.crosswalks import csf_for

OSCAL_VERSION = "1.2.1"
NS = "https://github.com/chaffed/OpenClear/ns/oscal"
CATALOG = "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
_ROOT = uuid.uuid5(uuid.NAMESPACE_URL, NS)
ROLES = {"bank": "bank", "customer": "business-customer", "host": "host"}  # a CUEC's party: its OSCAL role


def _uuid(*parts) -> str:
    return str(uuid.uuid5(_ROOT, "/".join(str(p) for p in parts)))


def _when(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def _app_version() -> str:
    try:
        return version("openclear")
    except PackageNotFoundError:
        return "unknown"


def _prop(name: str, value: str, **extra) -> dict:
    return {"name": name, "ns": NS, "value": str(value), **extra}


def _metadata(title: str, now: datetime, **extra) -> dict:
    return {"title": title, "last-modified": _when(now), "version": _app_version(), "oscal-version": OSCAL_VERSION, **extra}


# --- Component definition ----------------------------------------------------------------------


def component_definition(now: datetime | None = None) -> dict:
    """OpenClear's controls, and the CUECs they rely on, as an OSCAL component definition."""
    now = now or datetime.now(timezone.utc)
    live = [c for c in controls.CONTROLS if not c.withdrawn]
    current = [c for c in cuecs.CUECS if not c.withdrawn]
    order = list(cuecs.NIST_TITLES)
    used = sorted({n for c in live for n in c.nist} | {c.nist for c in current}, key=order.index)
    requirements = []
    for nist in used:
        ours = [c for c in live if nist in c.nist]
        theirs = [c for c in current if c.nist == nist]
        parts = []
        if ours:
            parts.append("OpenClear: " + "; ".join(c.title for c in ours) + ".")
        if theirs:
            parts.append("Relies on " + ", ".join(f"{c.id} ({cuecs.PARTIES[c.party].split(':')[0].lower()})" for c in theirs) + ".")
        roles = (["provider"] if ours else []) + sorted({ROLES[c.party] for c in theirs})
        requirements.append({
            "uuid": _uuid("requirement", nist), "control-id": cuecs.oscal_id(nist), "description": " ".join(parts),
            "props": [_prop("openclear-control", c.id, remarks=f"{c.title}. {c.statement}") for c in ours]
                     + [_prop("cuec", c.id, **{"class": ROLES[c.party]}, remarks=f"{c.title}. {c.statement}") for c in theirs]
                     + [_prop("csf-subcategory", k) for k in csf_for(nist)],
            "responsible-roles": [{"role-id": r} for r in roles],
        })
    roles = [{"id": "provider", "title": "OpenClear, the software"}, {"id": "bank", "title": cuecs.PARTIES["bank"]},
             {"id": "business-customer", "title": cuecs.PARTIES["customer"]}, {"id": "host", "title": cuecs.PARTIES["host"]}]
    return {"component-definition": {
        "uuid": _uuid("component-definition", _app_version(), controls.VERSION, cuecs.VERSION),
        "metadata": _metadata("OpenClear: controls and complementary user entity controls", now, roles=roles,
                              props=[_prop("control-inventory-version", controls.VERSION), _prop("cuec-inventory-version", cuecs.VERSION)]),
        "components": [{
            "uuid": _uuid("component", "openclear"), "type": "software", "title": "OpenClear",
            "description": "Positive pay for checks and ACH, for banks and their business customers.",
            "responsible-roles": [{"role-id": "provider"}],
            "control-implementations": [{
                "uuid": _uuid("control-implementation", "sp800-53r5"), "source": CATALOG,
                "description": f"OpenClear's controls and the CUECs they rely on, filed under {cuecs.NIST_CATALOG}. Each "
                               "`openclear-control` property is something OpenClear does; each `cuec` property is something the "
                               "party in its class must do for OpenClear's controls to work.",
                "implemented-requirements": requirements,
            }],
        }],
    }}


# --- Assessment results ------------------------------------------------------------------------


def _control_of_check() -> dict[str, str]:
    """Each check's main 800-53 control: the first NIST control of the first OpenClear control it tests."""
    out: dict[str, str] = {}
    for control in controls.CONTROLS:
        for key in control.checks:
            out.setdefault(key, control.nist[0])
    return out


def assessment_results(session: Session, tenant_id: uuid.UUID, start: date, end: date, now: datetime | None = None) -> dict:
    """A bank's control testing over [start, end] (inclusive): what ran, what deviated, what was attested."""
    from openclear.compliance import checks
    from openclear.domain.control_result import ControlDeviation, ControlTestResult
    from openclear.domain.cuec_attestation import CuecAttestation
    from openclear.domain.tenant import Tenant
    from openclear.services import attestation_service

    now = now or datetime.now(timezone.utc)
    begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    tenant = session.get(Tenant, tenant_id)
    bank, tool = _uuid("party", tenant_id), _uuid("party", "openclear")
    registry = checks.by_key()
    main_control = _control_of_check()

    observations, by_check = [], {}
    results = session.execute(select(ControlTestResult).where(
        ControlTestResult.tenant_id == tenant_id, ControlTestResult.recorded_at >= begin, ControlTestResult.recorded_at < finish)
        .order_by(ControlTestResult.check_key, ControlTestResult.recorded_at)).scalars().all()
    for result in results:
        by_check.setdefault(result.check_key, []).append(result)
    for key, rows in by_check.items():
        check = registry.get(key)
        counts = {s: sum(r.status == s for r in rows) for s in ("PASS", "WARN", "FAIL")}
        digests = sorted(r.evidence_sha256 for r in rows)
        observation = {
            "uuid": _uuid("observation", tenant_id, key, start, end), "title": check.title if check else key,
            "description": f"{len(rows)} results of the check `{key}` ({counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL); "
                           f"the latest: {rows[-1].status}, {rows[-1].summary}",
            "props": [_prop("check", key), _prop("results", len(rows)), _prop("results-sha256", hashlib.sha256("".join(digests).encode()).hexdigest()),
                      *(_prop(f"{s.lower()}-results", n) for s, n in counts.items())],
            "methods": ["TEST"], "types": ["control-objective"],
            "origins": [{"actors": [{"type": "party", "actor-uuid": tool}]}],
            "collected": _when(rows[-1].recorded_at),
        }
        observations.append(observation)

    findings = []
    deviations = session.execute(select(ControlDeviation).where(
        ControlDeviation.tenant_id == tenant_id, ControlDeviation.opened_at < finish,
        (ControlDeviation.closed_at.is_(None)) | (ControlDeviation.closed_at >= begin)).order_by(ControlDeviation.opened_at)).scalars().all()
    for d in deviations:
        nist = main_control.get(d.check_key)
        if nist is None:
            continue
        lasted = f"from {_when(d.opened_at)} to {_when(d.closed_at)}" if d.closed_at else f"from {_when(d.opened_at)}, still open"
        text = f"The check `{d.check_key}` was at {d.status} {lasted}."
        if d.cause:
            text += f" Cause: {d.cause}"
        if d.response:
            text += f" Response: {d.response}"
        finding = {
            "uuid": str(d.id), "title": f"{d.check_key} at {d.status}", "description": text,
            "props": [_prop("deviation", d.id), _prop("check", d.check_key), _prop("responded", "yes" if d.response else "no")],
            "origins": [{"actors": [{"type": "party", "actor-uuid": tool}]}],
            "target": {"type": "objective-id", "target-id": f"{cuecs.oscal_id(nist)}_obj",
                       "status": {"state": "not-satisfied", "reason": "fail"}},
        }
        if d.check_key in by_check:
            finding["related-observations"] = [{"observation-uuid": _uuid("observation", tenant_id, d.check_key, start, end)}]
        findings.append(finding)

    statements = []
    rows = session.execute(select(CuecAttestation).where(
        CuecAttestation.tenant_id == tenant_id, CuecAttestation.status == "accepted",
        CuecAttestation.period_end >= start, CuecAttestation.period_start <= end)
        .order_by(CuecAttestation.cuec_id, CuecAttestation.period_end)).scalars().all()
    if "host" not in attestation_service.parties_for(None):
        rows += [a for a in attestation_service.installation_attestations(session)
                 if a.status == "accepted" and a.period_end >= start and a.period_start <= end]
    for a in rows:
        cuec = cuecs.by_id()[a.cuec_id]
        who = "the installation's platform staff" if a.tenant_id is None else ("a customer" if a.customer_id else "the bank")
        prose = a.statement or f"OpenClear's checks show {cuec.title.lower()} {a.result.replace('_', ' ')} over the period."
        statements.append({
            "responsible-parties": [{"role-id": "attester", "party-uuids": [tool if a.tenant_id is None else bank]}],
            "parts": [{"name": "attestation", "ns": NS, "title": f"{a.cuec_id}: {a.result.replace('_', ' ')}",
                       "prose": f"{prose} (Attested by {who}, {a.method}, for {a.period_start} to {a.period_end}.)",
                       "props": [_prop("attestation", a.id), _prop("cuec", a.cuec_id, **{"class": ROLES[cuec.party]}),
                                 _prop("result", a.result), _prop("method", a.method), _prop("sha256", a.sha256 or "")]}],
        })

    plan = str(uuid.uuid4())  # a version 4, as OSCAL tools look for in a #reference
    reviewed = sorted({n for c in controls.CONTROLS if not c.withdrawn for n in c.nist} | {c.nist for c in cuecs.CUECS if not c.withdrawn},
                      key=list(cuecs.NIST_TITLES).index)
    result = {
        "uuid": _uuid("result", tenant_id, start, end), "title": f"Control testing, {start} to {end}",
        "description": f"{tenant.name}'s control testing in OpenClear from {start} to {end}: every scheduled run of each check, "
                       "each deviation, and each accepted CUEC attestation.",
        "start": _when(begin), "end": _when(finish),
        "reviewed-controls": {"control-selections": [{"include-controls": [{"control-id": cuecs.oscal_id(n)} for n in reviewed]}]},
    }
    for key, value in (("attestations", statements), ("observations", observations), ("findings", findings)):
        if value:
            result[key] = value
    return {"assessment-results": {
        "uuid": str(uuid.uuid4()),
        "metadata": _metadata(f"{tenant.name}: control testing, {start} to {end}", now,
                              roles=[{"id": "attester", "title": "Attests a CUEC operated over the period"}],
                              parties=[{"uuid": bank, "type": "organization", "name": tenant.name},
                                       {"uuid": tool, "type": "organization", "name": "OpenClear"}]),
        "import-ap": {"href": f"#{plan}"},
        "results": [result],
        "back-matter": {"resources": [{
            "uuid": plan, "title": "No separate assessment plan",
            "description": "OpenClear's checks run on their own schedule (hourly or nightly, compliance/checks.py), and each "
                           "scheduled run is kept; these results are those runs."}]},
    }}
