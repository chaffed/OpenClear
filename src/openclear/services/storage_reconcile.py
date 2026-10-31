# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Rows and files, checked against each other (CONTINUITY_PLAN.md BC-D3).

The database and file storage are backed up separately, so a restore can leave rows naming files
that aren't there (uploads, check images, exports, decision files written after the snapshot) and
files no row names. `reconcile` walks every table that keeps a storage reference and every storage
area, and says which is which: run by the restore drill, by an operator after a restore, and
nightly as a warning-only check so drift is seen before a disaster."""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.db.base import Base
from openclear.db.tenancy import bind_session_to_tenant

logger = logging.getLogger(__name__)

# Every column that holds a storage reference, by table, with the area it's kept in.
REFERENCES: dict[str, tuple[tuple[str, str], ...]] = {
    "check_image": (("front_image_path", "check_images"), ("back_image_path", "check_images")),
    "bulk_upload_file": (("storage_path", "bulk_uploads"),),
    "data_export_job": (("archive_path", "data_exports"),),
    "decision_batch": (("storage_ref", "decision_files"),),
    "ml_model": (("artifact_path", "ml_artifacts"),),
    "tenant": (("logo_path", "tenant_assets"), ("favicon_path", "tenant_assets")),
}
# Areas nothing references by row: OpenClear's own working files, not reconciled.
UNREFERENCED_AREAS = frozenset({"audit_archives", "backup_manifests"})


@dataclass
class Reconciliation:
    rows_checked: int = 0
    files_checked: int = 0
    missing: list[dict] = field(default_factory=list)   # a row whose file isn't there: {table, id, tenant_id, column, ref}
    orphans: dict[str, list[str]] = field(default_factory=dict)  # files no row names, by area: keys
    unreachable: list[str] = field(default_factory=list)  # areas storage couldn't list

    @property
    def ok(self) -> bool:
        return not self.missing and not self.unreachable

    @property
    def orphan_count(self) -> int:
        return sum(len(v) for v in self.orphans.values())

    def summary(self) -> str:
        parts = [f"{self.rows_checked} references checked"]
        parts.append(f"{len(self.missing)} missing" if self.missing else "none missing")
        parts.append(f"{self.orphan_count} files with no row" if self.orphan_count else "no files without a row")
        if self.unreachable:
            parts.append(f"couldn't list: {', '.join(self.unreachable)}")
        return "; ".join(parts) + "."

    def as_dict(self) -> dict:
        return {"rows_checked": self.rows_checked, "files_checked": self.files_checked, "missing": self.missing[:500],
                "missing_count": len(self.missing), "orphans": {a: k[:200] for a, k in self.orphans.items()},
                "orphan_count": self.orphan_count, "unreachable": self.unreachable}


def reconcile(session: Session, *, list_files: bool = True) -> Reconciliation:
    """Every bank's rows (bound to each in turn, for row-level security) against the files.
    Leaves the session unbound. With `list_files` off, only rows are checked (quick, nightly)."""
    from openclear.domain.tenant import Tenant

    result = Reconciliation()
    referenced: set[str] = set()
    bind_session_to_tenant(session, None)
    tenant_ids = list(session.execute(select(Tenant.id)).scalars())
    for name, columns in REFERENCES.items():
        table = Base.metadata.tables[name]
        scopes = tenant_ids if "tenant_id" in table.columns and name != "tenant" else [None]
        for tenant_id in scopes:
            bind_session_to_tenant(session, tenant_id)
            stmt = select(table.c.id, *(table.c[c] for c, _area in columns), *([table.c.tenant_id] if tenant_id else []))
            if tenant_id:
                stmt = stmt.where(table.c.tenant_id == tenant_id)
            for row in session.execute(stmt):
                for index, (column, _area) in enumerate(columns):
                    ref = row[1 + index]
                    if not ref:
                        continue
                    result.rows_checked += 1
                    referenced.add(str(ref))
                    if not storage.exists(str(ref)):
                        result.missing.append({"table": name, "id": str(row[0]), "tenant_id": str(tenant_id or row[0]) if name == "tenant" else str(tenant_id),
                                               "column": column, "ref": str(ref)})
    bind_session_to_tenant(session, None)
    if list_files:
        for area in storage.AREAS:
            if area in UNREFERENCED_AREAS:
                continue
            try:
                keys = storage.list_keys(area)
            except Exception:  # noqa: BLE001 -- the store couldn't be listed: said, not raised
                logger.exception("Reconciliation: storage area %s couldn't be listed", area)
                result.unreachable.append(area)
                continue
            result.files_checked += len(keys)
            lost = [k for k in keys if storage.ref(area, k) not in referenced and not k.startswith("_ready/")]
            if lost:
                result.orphans[area] = sorted(lost)
    return result


def consistency_check(session: Session):
    """The nightly control check (installation scope, WARN only): rows whose files are gone, and
    areas that couldn't be listed. Files without rows are reported, not warned about: a newer
    restore's, or an upload still being written."""
    from openclear.services.controls_service import Control, PASS, WARN

    title = "Files and rows agree"
    found = reconcile(session)
    evidence = found.as_dict()
    if found.missing or found.unreachable:
        return Control("storage_consistency", title, WARN, found.summary() + " A row whose file is gone means a restore or a storage change "
                       "left the two apart; Platform → Continuity lists them.", evidence)
    return Control("storage_consistency", title, PASS, found.summary(), evidence)
