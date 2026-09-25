#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Mints a new cross-tenant platform API key. By default it can only read the
usage-metering endpoint (GET /api/v1/platform/usage — see services/usage_metrics_service.py).
Pass --scope shared_model for the platform operator's key that runs the shared
fraud-scoring model (/api/v1/platform/ml/* — see api/v1/platform_ml.py). Give each key only
what it needs. There's no
platform-level admin UI in this app (every admin surface is tenant-scoped by design),
so minting a key — a rare, ops-only action — is a management script instead.

The raw key is printed exactly once, here. Only its hash is ever stored; if it's lost,
revoke it (services/platform_api_key_service.py::revoke) and run this again for a
replacement.

Usage:
    python scripts/create_metering_api_key.py "Acme Billing Integration"
    python scripts/create_metering_api_key.py "Platform ML operator" --scope shared_model
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    os.chdir(PROJECT_ROOT)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="A human label identifying who/what this key is for (e.g. 'Acme Billing Integration')")
    parser.add_argument(
        "--scope", action="append", choices=["usage", "shared_model"], dest="scopes",
        help="What the key may do; repeat for more than one (default: usage)",
    )
    args = parser.parse_args()

    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "production")

    from openclear.db.session import get_session_factory
    from openclear.services import platform_api_key_service

    session = get_session_factory()()
    try:
        row, raw_key = platform_api_key_service.generate_and_create(session, args.name, tuple(args.scopes or ["usage"]))
        session.commit()
    finally:
        session.close()

    print(f"Created platform API key {row.id} ({args.name!r}).\n")
    print("Raw key (shown once — store it now, it cannot be retrieved again):\n")
    print(f"  {raw_key}\n")
    print(f"Scopes: {', '.join(row.scopes)}. Send it as the X-Api-Key header.")


if __name__ == "__main__":
    sys.exit(main())
