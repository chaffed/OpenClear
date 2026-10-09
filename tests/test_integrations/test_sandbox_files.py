# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The sandbox's synthetic test files (scripts/sandbox_test_files.py, SDK_PLAN.md P4): built
by Moov's ACH and imagecashletter servers, then imported by OpenClear, where every item must
come out as expected.csv says. Skipped unless both Moov servers are running."""

import csv
import importlib.util
import os
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from openclear.domain.account import Account
from openclear.domain.exception_item import ExceptionItem
from openclear.services import api_client_service, security_group_service

from openclear_sdk import OpenClear  # noqa: E402 -- tests/test_integrations/conftest.py

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sandbox_test_files", ROOT / "scripts" / "sandbox_test_files.py")
files = importlib.util.module_from_spec(_spec)
sys.modules["sandbox_test_files"] = files  # dataclasses look their module up
_spec.loader.exec_module(files)
ICL, ACH = os.environ.get("MOOV_ICL_URL", "http://localhost:18083"), os.environ.get("MOOV_ACH_URL", "http://localhost:18080")
CHECKSIM = Path(os.environ.get("CHECKSIM_OUTPUT", ROOT.parent / "CheckSim" / "output"))


def _moov_running() -> bool:
    try:
        return all(httpx.get(f"{url}/ping", timeout=2).status_code == 200 for url in (ICL, ACH))
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _moov_running(), reason="needs Moov ACH (:18080) and imagecashletter (:18083) running")


@pytest.mark.parametrize("source", ["built_in", "checksim"])
def test_generated_files_import_as_expected(source, client, db_session, tenant_factory, tmp_path):
    if source == "checksim" and not (CHECKSIM / "manifest.csv").exists():
        pytest.skip("CheckSim output not found")
    tenant, _a, _u = tenant_factory.make(slug=f"sbx-files-{source.replace('_', '-')}")
    db_session.add(Account(tenant_id=tenant.id, account_number="1000234561", name="Sandbox Operating"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Loader", security_group_id=group.id)
    db_session.commit()

    out = tmp_path / source
    argv = ["--out", str(out), "--account", "1000234561", "--count", "8", "--moov-icl", ICL, "--moov-ach", ACH]
    if source == "checksim":
        argv += ["--checksim", str(CHECKSIM)]
    assert files.main(argv) == 0

    oc = OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client)
    for kind, name, fmt in (("issued_items", "issued_items.csv", "tabular"), ("check_images", "presented.x937", "x937"),
                            ("ach_transactions", "ach_debits.ach", "nacha")):
        upload = oc.bulk.upload_file(kind, fmt, (name, (out / name).read_bytes()))
        failures = [r["error"] for r in oc.bulk_uploads.iter_get_bulk_upload_results(upload["id"], failures_only=True)]
        assert upload["failed"] == 0, (name, failures[:3])

    expected = list(csv.DictReader((out / "expected.csv").open()))
    raised = [e.exception_types for e in db_session.execute(select(ExceptionItem).where(ExceptionItem.tenant_id == tenant.id)).scalars()]
    checks = [row for row in expected if row["kind"] == "check"]
    for wanted in {row["expected"] for row in checks} - {"matched"}:
        code = wanted.split(" ")[0]
        assert any(code in types for types in raised), (wanted, raised)
    ach_exceptions = [t for t in raised if "unauthorized_originator" in t]
    assert len(ach_exceptions) == len([row for row in expected if row["kind"] == "ach"])
