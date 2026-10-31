#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The restore drill (CONTINUITY_PLAN.md BC-D2; CUEC H-CP-9-01): proves a backup restores.

    scripts/restore_drill.py <dump> --scratch <database url> [--manifest latest.json]

`dump` is a SQLite copy (backup_manifests/sqlite/…, or any .sqlite file) or a pg_dump custom-format
archive; `--scratch` is a throwaway database to restore into (for SQLite, a path the copy is put
at; for Postgres, an empty database `pg_restore` can write to). The drill: restores the dump;
runs the migrations to head; verifies every bank's action-log chain; reconciles the restored rows
against file storage; checks the dump's hash against the manifest when one is given; then writes
a signed RestoreTest row into the live database (OPENCLEAR_DATABASE_URL), which the Controls page
shows as the last restore test. Exit status 1 when any step fails. Never touches the live
database's own tables except restore_test."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def restore(dump: Path, scratch_url: str) -> dict:
    from sqlalchemy.engine import make_url

    url = make_url(scratch_url)
    if url.get_backend_name() == "sqlite":
        target = Path(url.database)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dump, target)
        return {"method": "copied the SQLite file", "target": str(target)}
    completed = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "-d", scratch_url, str(dump)],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {completed.stderr.strip()[-2000:]}")
    return {"method": "pg_restore --clean --if-exists", "target": url.render_as_string(hide_password=True)}


def migrate(scratch_url: str) -> dict:
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", scratch_url.replace("%", "%%"))
    engine = create_engine(scratch_url)
    with engine.connect() as conn:
        before = MigrationContext.configure(conn).get_current_revision()
    command.upgrade(cfg, "head")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    engine.dispose()
    return {"from": before, "to": head}


def verify_chains(scratch_url: str) -> dict:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.tenant import Tenant
    from openclear.services import audit_log_service

    engine = create_engine(scratch_url)
    banks, broken = 0, []
    with Session(engine) as session:
        for tenant_id, slug in session.execute(select(Tenant.id, Tenant.slug)):
            bind_session_to_tenant(session, tenant_id)
            outcome = audit_log_service.verify_chain(session, tenant_id)
            banks += 1
            if not outcome.valid:
                broken.append({"bank": slug, "checked": outcome.checked_count, "broken_at": str(outcome.broken_at_entry_id)})
    engine.dispose()
    return {"banks": banks, "broken": broken}


def reconcile(scratch_url: str) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from openclear.services import storage_reconcile

    engine = create_engine(scratch_url)
    with Session(engine) as session:
        found = storage_reconcile.reconcile(session)
    engine.dispose()
    return {"ok": found.ok, "summary": found.summary(), **found.as_dict()}


def run_drill(dump: Path, scratch_url: str, manifest: dict | None = None, *, record_in=None) -> tuple[bool, dict]:
    """Every step, each recorded; `record_in` is a Session on the live database for the RestoreTest row."""
    from openclear.domain.restore_test import RestoreTest
    from openclear.services import evidence_service

    steps: dict = {"dump": str(dump), "dump_bytes": dump.stat().st_size, "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest()}
    ok = True
    if manifest is not None:
        stated = (manifest.get("database") or {}).get("sha256")
        steps["manifest"] = {"completed_at": manifest.get("completed_at"), "sha256_matches": stated is None or stated == steps["dump_sha256"]}
        ok &= steps["manifest"]["sha256_matches"]
    for name, step in (("restore", lambda: restore(dump, scratch_url)), ("migrate", lambda: migrate(scratch_url)),
                       ("chains", lambda: verify_chains(scratch_url)), ("files", lambda: reconcile(scratch_url))):
        try:
            steps[name] = step()
        except Exception as exc:  # noqa: BLE001 -- the step's failure is the drill's finding
            steps[name] = {"error": f"{type(exc).__name__}: {exc}"[:2000]}
            ok = False
            break
    if "chains" in steps and steps["chains"].get("broken"):
        ok = False
    if "files" in steps and steps["files"].get("ok") is False:
        ok = False
    steps["ok"], steps["run_at"] = ok, datetime.now(timezone.utc).isoformat()
    if record_in is not None:
        completed = manifest.get("completed_at") if manifest else None
        signed = evidence_service.sign({"type": "OpenClear restore drill", **steps})
        record_in.add(RestoreTest(ok=ok, source=str(dump)[:1000], detail=steps, certificate=signed, certificate_sha256=signed["signature"]["sha256"],
                                  backup_completed_at=datetime.fromisoformat(completed) if completed else None))
        record_in.commit()
    return ok, steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dump", type=Path)
    parser.add_argument("--scratch", required=True, help="a throwaway database URL to restore into")
    parser.add_argument("--manifest", type=Path, help="the backup's latest.json, to check the dump's hash")
    parser.add_argument("--no-record", action="store_true", help="don't write the result to the live database")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text()) if args.manifest else None
    session = None
    if not args.no_record:
        from sqlalchemy.orm import Session

        from openclear.db.session import get_engine

        session = Session(get_engine())
    ok, steps = run_drill(args.dump, args.scratch, manifest, record_in=session)
    print(json.dumps(steps, indent=2, default=str))
    print("RESTORE DRILL", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
