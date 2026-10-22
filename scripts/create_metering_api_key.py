#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Makes a new cross-tenant platform API key. By default it can only read the
usage-metering endpoint (GET /api/v1/platform/usage — see services/usage_metrics_service.py).
Pass --scope operations for monitoring (jobs, metrics, security events) or --scope billing
for platform billing. Give each key only what it needs.

A new key reaches every bank's data, so a second platform operator approves it
(DUAL_CONTROL_PLAN.md DC-D12) at /ui/platform/changes. The raw key is printed here, once, and
works from the approval; only its hash is ever stored. With no operator to approve, it works
after 24 hours. `--emergency` makes it work at once, as a critical security event in the
provider's action log: for the installation's first key, not routine use. If a key is lost,
revoke it (services/platform_api_key_service.py::revoke) and run this again for a replacement.

Usage:
    python scripts/create_metering_api_key.py "Acme Billing Integration"
    python scripts/create_metering_api_key.py "Monitoring" --scope operations
    python scripts/create_metering_api_key.py "First operator key" --scope operations --emergency
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(PROJECT_ROOT)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="A human label identifying who/what this key is for (e.g. 'Acme Billing Integration')")
    parser.add_argument(
        "--scope", action="append", choices=["usage", "operations", "billing"], dest="scopes",
        help="What the key may do; repeat for more than one (default: usage)",
    )
    parser.add_argument("--emergency", action="store_true", help="make the key work at once, as a critical security event")
    args = parser.parse_args()

    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "production")

    from openclear.db.session import get_session_factory
    from openclear.services import change_request_service, platform_api_key_service, platform_log_service, security_events

    scopes = tuple(args.scopes or ["usage"])
    session = get_session_factory()()
    try:
        platform_api_key_service.check_scopes(scopes)
        raw_key, key_hash = platform_api_key_service.mint()
        if args.emergency:
            row = platform_api_key_service.create_from_hash(session, args.name, scopes, key_hash)
            summary = f"Platform API key {args.name!r} ({', '.join(row.scopes)}), working at once with --emergency (no second operator)"
            platform_log_service.record(session, action="platform.api_key.emergency", summary=summary, resource_type="platform_api_key",
                                        resource_id=row.id)
            security_events.emit(session, "change_request.applied_alone", tenant_id=None, actor_type="system", target_type="platform_api_key",
                                 target_id=row.id, details={"kind": "platform.api_key", "summary": summary}, channel="platform")
            outcome = None
        else:
            outcome = change_request_service.request_or_apply(
                session, "platform.api_key", {"name": args.name, "scopes": sorted(set(scopes)), "key_hash": key_hash}, tenant_id=None,
                user_id=None, channel="system", requested_with="scripts/create_metering_api_key.py",
                summary=f"Make platform API key {args.name!r} ({', '.join(sorted(set(scopes)))})")
        session.commit()
    except (ValueError, change_request_service.ChangeRequestError) as exc:
        print(f"Not done: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print("Raw key (shown once — store it now, it cannot be retrieved again):\n")
    print(f"  {raw_key}\n")
    print(f"Scopes: {', '.join(sorted(set(scopes)))}. Send it as the X-Api-Key header.")
    if outcome is None:
        print("It works now (--emergency).")
    elif outcome.applied:
        print("It works now.")
    elif outcome.change.applies_at:
        print(f"It works from {outcome.change.applies_at:%Y-%m-%d %H:%M} UTC, as no operator can approve it yet. Use --emergency to make it "
              "work now.")
    else:
        print("It works once another operator approves it at /ui/platform/changes.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
