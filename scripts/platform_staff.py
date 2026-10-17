#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Makes someone platform staff, or stops them being it (UX_PLAN.md U8, U9). An operator
sees every bank at /ui/platform and sets up new ones; a support person works in a bank only
through access that bank grants them (Settings → Support access). Both sign in at
/ui/platform/login. No screen can do this: it's for whoever runs the installation.

A person new to OpenClear is created, with a password read from the terminal (or
OPENCLEAR_STAFF_PASSWORD). Changing a role signs them out everywhere.

Giving someone a role, or raising support to operator, waits for one of the platform's operators
to approve it (/ui/platform/changes; DUAL_CONTROL_PLAN.md DC4); with no operator yet, it takes
effect after 24 hours. `--emergency` applies it at once, as a critical security event in the
provider's action log: for the installation's first operator, or when operators are locked out.
Removing a role never waits. Each is in the provider's action log.

Usage:
    python scripts/platform_staff.py ops@yourcompany.example --role operator
    python scripts/platform_staff.py ops@yourcompany.example --role operator --emergency
    python scripts/platform_staff.py helpdesk@yourcompany.example --role support
    python scripts/platform_staff.py helpdesk@yourcompany.example --remove
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(PROJECT_ROOT)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--role", choices=["operator", "support"])
    group.add_argument("--remove", action="store_true", help="no longer platform staff")
    parser.add_argument("--emergency", action="store_true", help="apply a new role at once, as a critical security event")
    args = parser.parse_args()
    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "production")

    from openclear.db.session import get_session_factory
    from openclear.domain.user import User
    from openclear.services import support_access_service

    from openclear.auth.security import hash_password
    from openclear.services import change_request_service, platform_log_service, security_events

    session = get_session_factory()()
    email = args.email.strip().lower()
    try:
        user = session.query(User).filter(User.email == email).first()
        if args.remove:
            user, _created = support_access_service.set_platform_role(session, email, None)
            platform_log_service.record(session, action="platform.role", summary=f"{email} is no longer platform staff (scripts/platform_staff.py)",
                                        resource_type="user", resource_id=user.id)
            session.commit()
            print(f"{email} is no longer platform staff.")
            return 0
        if user is None:
            password = os.environ.get("OPENCLEAR_STAFF_PASSWORD") or getpass.getpass("Password for this new person: ")
            if not password or len(password) < 12:
                raise support_access_service.SupportAccessError("A new person needs a password of at least 12 characters.")
            user = User(email=email, hashed_password=hash_password(password))  # no role until it's approved
            session.add(user)
            session.flush()
        if args.emergency:
            support_access_service.set_platform_role(session, email, args.role)
            platform_log_service.record(session, action="platform.role.emergency", resource_type="user", resource_id=user.id,
                                        summary=f"{email}: platform {args.role}, applied at once with --emergency (no second operator)")
            security_events.emit(session, "change_request.applied_alone", tenant_id=None, actor_type="system", target_type="user",
                                 target_id=user.id, details={"kind": "platform.role", "summary": f"{email}: platform {args.role} (--emergency)"},
                                 channel="platform")
            session.commit()
            print(f"{email}: platform {args.role}, at once (--emergency). Sign in at /ui/platform/login.")
            return 0
        outcome = change_request_service.request_or_apply(session, "platform.role", {"email": email, "role": args.role}, tenant_id=None,
                                                          user_id=None, channel="system", requested_with="scripts/platform_staff.py",
                                                          summary=f"Make {email} platform {args.role}")
        session.commit()
    except (support_access_service.SupportAccessError, change_request_service.ChangeRequestError) as exc:
        print(f"Not done: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
    if outcome.applied:
        print(f"{email}: platform {args.role}.")
    elif outcome.change.applies_at:
        print(f"Asked: {email} becomes platform {args.role} on {outcome.change.applies_at:%Y-%m-%d %H:%M} UTC, as no operator can approve it "
              "yet. Use --emergency to apply it now.")
    else:
        print(f"Asked: an operator approves {email} becoming platform {args.role} at /ui/platform/changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
