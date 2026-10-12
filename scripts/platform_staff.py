#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Makes someone platform staff, or stops them being it (UX_PLAN.md U8, U9). An operator
sees every bank at /ui/platform and sets up new ones; a support person works in a bank only
through access that bank grants them (Settings → Support access). Both sign in at
/ui/platform/login. No screen can do this: it's for whoever runs the installation.

A person new to OpenClear is created, with a password read from the terminal (or
OPENCLEAR_STAFF_PASSWORD). Changing a role signs them out everywhere.

Usage:
    python scripts/platform_staff.py ops@yourcompany.example --role operator
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
    args = parser.parse_args()
    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "production")

    from openclear.db.session import get_session_factory
    from openclear.domain.user import User
    from openclear.services import support_access_service

    session = get_session_factory()()
    try:
        exists = session.query(User).filter(User.email == args.email.strip().lower()).first() is not None
        password = None
        if not exists and not args.remove:
            password = os.environ.get("OPENCLEAR_STAFF_PASSWORD") or getpass.getpass("Password for this new person: ")
        user, created = support_access_service.set_platform_role(session, args.email, None if args.remove else args.role,
                                                                 password=password)
        session.commit()
    except support_access_service.SupportAccessError as exc:
        print(f"Not done: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
    if args.remove:
        print(f"{user.email} is no longer platform staff.")
    else:
        print(f"{'Created' if created else 'Updated'} {user.email}: platform {args.role}. Sign in at /ui/platform/login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
