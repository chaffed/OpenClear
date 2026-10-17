# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The control and CUEC inventories (CONTROLS_PLAN.md CT1; compliance/controls.py, cuecs.py),
read-only, with their NIST CSF 2.0 crosswalk (CT6). Mappings, results and attestations arrive
with CT2 to CT5 under the same prefix.
`GET /api/v1/controls/status` stays as it is (CT-D14)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from openclear.auth.deps import get_current_context, require_permission
from openclear.compliance import controls, cuecs
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import controls_service

router = APIRouter(prefix="/compliance", tags=["compliance"])

Status = Literal["PASS", "WARN", "FAIL"]


class NistControl(BaseModel):
    id: str
    title: str
    oscal_id: str


class CsfSubcategory(BaseModel):
    id: str  # PR.AA-05
    title: str


class ControlCheck(BaseModel):
    key: str
    status: Literal["live", "planned"]
    title: str | None  # a live check's title on the Controls page
    result: Status | None  # a live check's status now; None for a planned one
    detail: str | None


class InventoryControl(BaseModel):
    id: str
    family: str
    title: str
    statement: str
    nist: list[NistControl]
    type: Literal["preventive", "detective", "corrective"]
    nature: Literal["built_in", "configurable"]
    checks: list[ControlCheck]
    csf: list[CsfSubcategory]  # the NIST CSF 2.0 subcategories NIST maps to its 800-53 controls
    cuecs: list[str]
    where: str
    withdrawn: bool
    status: Status | None  # the worst of its live checks; None when it has none yet


class ControlInventory(BaseModel):
    version: str
    catalog: str
    checked_at: str
    controls: list[InventoryControl]


class InventoryCuec(BaseModel):
    id: str
    party: Literal["bank", "customer", "host"]
    nist: str
    nist_title: str
    oscal_id: str
    related: list[str]
    title: str
    statement: str
    complements: str
    evidence: Literal["automated", "assisted", "attested"]
    check: str | None
    check_status: Literal["live", "planned"] | None
    frequency: str
    csf: list[CsfSubcategory]  # the NIST CSF 2.0 subcategories NIST maps to its 800-53 control
    where: str
    withdrawn: bool
    controls: list[str]  # the OpenClear controls that rely on it


class CuecInventory(BaseModel):
    version: str
    catalog: str
    cuecs: list[InventoryCuec]


_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


def inventory(live: list[controls_service.Control]) -> list[dict]:
    """Each control with its checks' results now (from controls_service), worst first among them."""
    results = {c.key: c for c in live}
    out = []
    for control in controls.CONTROLS:
        data = control.as_dict()
        for check in data["checks"]:
            found = results.get(check["key"])
            check["title"], check["result"], check["detail"] = (found.title, found.status, found.detail) if found else (None, None, None)
        seen = [c["result"] for c in data["checks"] if c["result"]]
        data["status"] = max(seen, key=_ORDER.__getitem__) if seen else None
        out.append(data)
    return out


@router.get("/controls", response_model=ControlInventory)
def control_inventory(db: Session = Depends(get_db), ctx: TenantContext = Depends(require_permission("audit_log:read"))) -> dict:
    """OpenClear's control inventory: each control with its NIST SP 800-53 controls, its
    checks (each live check's status for this bank now), the NIST CSF 2.0 subcategories NIST maps
    to those controls, and the CUECs it relies on. `status`
    is the worst of its live checks, or null when none of its checks is live yet."""
    if ctx.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Controls are for the bank's own staff.")
    live = controls_service.status(db, ctx.tenant_id)
    return {"version": controls.VERSION, "catalog": cuecs.NIST_CATALOG, "checked_at": controls_service.summary(live)["checked_at"],
            "controls": inventory(live)}


@router.get("/cuecs", response_model=CuecInventory)
def cuec_inventory(party: Literal["bank", "customer", "host"] | None = Query(None),
                   ctx: TenantContext = Depends(get_current_context)) -> dict:
    """The complementary user entity controls: what OpenClear's controls rely on the bank, its
    customers and whoever runs OpenClear to do, each filed under its NIST SP 800-53 control (with
    the CSF 2.0 subcategories NIST maps to it), with the OpenClear controls that rely on it. A customer's own session sees only the
    customer's. Filter with `party`."""
    if ctx.customer_id is not None:
        if party not in (None, "customer"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "A customer sees only its own CUECs.")
        party = "customer"
    relied_on: dict[str, list[str]] = {}
    for control in controls.CONTROLS:
        for cuec_id in control.cuecs:
            relied_on.setdefault(cuec_id, []).append(control.id)
    rows = [{**c.as_dict(), "controls": relied_on.get(c.id, [])} for c in cuecs.CUECS if party is None or c.party == party]
    return {"version": cuecs.VERSION, "catalog": cuecs.NIST_CATALOG, "cuecs": rows}
