# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What's locked in the shared public demo organization (Tenant.is_demo).

Everyone signs into the demo with the same published credentials, so an action that
changes how *other visitors* get in, or reaches outside the demo, would let one visitor
disrupt the rest until the next reset. Examples: changing the shared password,
registering a security key on the shared admin account (every later login would then
need that key), editing security groups or SSO, or pointing SSO at an internal address.
Those are refused. Everything else, the actual positive-pay workflow included, stays
fully usable, and the demo resets every hour
(workers/tasks.py::demo_reset_job) to undo whatever visitors changed.

One list, checked centrally for every signed-in, non-GET request to the web UI
(web/deps.py::get_web_context) and the API (auth/deps.py::get_current_context), so it
can be audited in one place and a new route can't forget to opt in.
tests/test_web/test_web_demo_guard.py checks each pattern still matches a real route."""

import re

DEMO_LOCKED_MESSAGE = (
    "That's turned off in the public demo, so one visitor can't lock out the others. "
    "Everything else works, and the demo resets every hour."
)

_UUID = r"[0-9a-fA-F-]{36}"

_LOCKED_PATTERNS = [
    # Sign-in configuration: SSO connections, their group mappings, password-login toggles.
    r"/ui/admin/sso(/.*)?",
    rf"/ui/customers/{_UUID}/sso(/.*)?",
    # Security groups (removing Admin's permissions would lock everyone out).
    r"/ui/security-groups(/.*)?",
    # Existing users' access, and granting access to people from other organizations.
    rf"/ui/users/{_UUID}(/(deactivate|reactivate|unlock|reset-password|sign-out))?",
    r"/ui/users/(access/grant|confirm|bulk/confirm)",
    # The signed-in (shared) account's own credentials and sessions.
    r"/ui/security/(password|sign-out-others|webauthn/.*)",
    r"/api/v1/auth/webauthn/(register/.*|credentials/.*)",
    # Organization branding and session timeouts, shown to / applied to every visitor.
    r"/ui/settings",
    r"/ui/settings/session-timeout",
    # Which fraud-scoring model the organization uses.
    r"/ui/settings/fraud-model/.*",
    # Heavy background work.
    r"/ui/settings/data-export/start",
    rf"/ui/customers/{_UUID}/data-export/start",
    # ML model retraining/activation.
    r"/ui/admin/ml/.*",
    rf"/ui/admin/customers/{_UUID}/ml/.*",
    r"/api/v1/admin/ml/.*",
]
_LOCKED = [re.compile(pattern) for pattern in _LOCKED_PATTERNS]

_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_locked_in_demo(method: str, path: str) -> bool:
    if method.upper() in _READ_ONLY_METHODS:
        return False
    return any(pattern.fullmatch(path) for pattern in _LOCKED)
