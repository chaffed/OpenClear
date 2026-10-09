#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Writes the API's OpenAPI description to openapi/openclear-v1.json (SDK_PLAN.md P0).

The file is committed: it's the contract integrators and the generated SDKs build on.
tests/test_openapi_contract.py regenerates it on every test run and fails on a breaking
change, or asks for this script to be run when the change is only additive.

    python scripts/export_openapi.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "openapi" / "openclear-v1.json"


def current_spec() -> dict:
    """The API's description as the running code generates it, without the version (which
    changes every release and isn't part of the contract)."""
    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "development")
    os.environ.setdefault("OPENCLEAR_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/spec.db")
    sys.path.insert(0, str(ROOT / "src"))
    from openclear.main import create_app

    spec = create_app().openapi()
    spec["info"] = {"title": spec["info"]["title"], "version": "v1"}
    spec["paths"] = {path: ops for path, ops in spec["paths"].items() if path.startswith("/api/v1/")}
    return spec


def render(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> None:
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(render(current_spec()))
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
