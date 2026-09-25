#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Runs one pass of the auto-import dropbox scan and exits — see
services/dropbox_import_service.py for the directory layout and isolation model. This is
what a Unix cron entry or a Windows Task Scheduler action should invoke; it does exactly
what the in-app scheduler's job does (workers/tasks.py::dropbox_import_job), just as a
one-shot process instead of a long-running background thread. Use this instead of the
in-app scheduler (OPENCLEAR_AUTO_IMPORT_ENABLED=false, the default) when you'd rather control
the schedule from the OS than from OpenClear's own config.

Run with the same environment (OPENCLEAR_* env vars, or a .env file in the working
directory) the app itself runs with — it needs the same database connection and the
same OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR:

    python scripts/import_dropbox.py

Exits 0 if the scan itself completed (even if individual files failed to import — those
are reported per-file, not treated as a run failure), 1 if the scan could not run at
all (e.g. a database connection error).
"""

import logging
import sys

from openclear.db.session import get_session_factory
from openclear.services.dropbox_import_service import scan_and_import_all_tenants

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("import_dropbox")


def main() -> int:
    session = get_session_factory()()
    try:
        results = scan_and_import_all_tenants(session)
    except Exception:
        logger.exception("Dropbox import scan failed to run")
        return 1
    finally:
        session.close()

    imported = [r for r in results if r.outcome == "imported"]
    duplicates = [r for r in results if r.outcome == "duplicate"]
    failed = [r for r in results if r.outcome == "failed"]

    for r in imported:
        print(f"  imported  {r.tenant_slug}/{r.customer_number or '-'}/{r.kind_subpath}/{r.filename} "
              f"(succeeded={r.succeeded_count}, failed={r.failed_count})")
    for r in duplicates:
        print(f"  duplicate {r.tenant_slug}/{r.customer_number or '-'}/{r.kind_subpath}/{r.filename} -- skipped")
    for r in failed:
        print(f"  failed    {r.tenant_slug}/{r.customer_number or '-'}/{r.kind_subpath}/{r.filename}: {r.error}")

    print(f"\n{len(imported)} file(s) imported, {len(duplicates)} duplicate(s) skipped, {len(failed)} file(s) failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
