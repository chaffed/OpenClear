# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The full catalog of permission keys a security group can grant, and the default
groups seeded into every new tenant. Replaces the old fixed `UserRole` enum + static
`PERMISSIONS` matrix (auth/rbac.py, now deleted) — permission sets are now per-tenant,
named, and editable (see services/security_group_service.py), not hardcoded roles.

Both channels (the JSON API's `require_permission` and the web UI's
`require_web_permission`) check membership in the same `TenantContext.permissions` set,
resolved fresh from a tenant's SecurityGroup row on every request (auth/deps.py) — so
these keys are the single source of truth for every gated action in the app, read or
write, across both channels."""

PERMISSION_CATALOG: dict[str, str] = {
    "account:read": "View accounts",
    "account:write": "Create accounts",
    "issued_item:read": "View issued items",
    "issued_item:write": "Create/void issued items",
    "stop_payment:read": "View stop payments",
    "stop_payment:write": "Create/cancel stop payments",
    "paid_item:read": "View paid items",
    "paid_item:write": "Submit paid items",
    "check_image:read": "View check images",
    "check_image:write": "Upload/reprocess check images",
    "ach_authorization:read": "View ACH authorizations",
    "ach_authorization:write": "Create/revoke ACH authorizations",
    "ach_transaction:read": "View ACH transactions",
    "ach_transaction:write": "Submit/bulk-load ACH transactions",
    "exception:read": "View exceptions",
    "exception:recommend": "Recommend pay/return decisions",
    "exception:decide": "Finalize pay/return decisions",
    "admin:manage": "Manage ML models and payment networks",
    "user:manage": "Manage users",
    "security_group:manage": "Manage security groups",
    "tenant:manage": "Manage organization branding and settings",
    "audit_log:read": "View the immutable action log",
    "customer:manage": "Manage customers",
    "data_export:run": "Export all tenant/customer data for migration or offboarding",
    "ach_return_reason:manage": "Manage the catalog of ACH return reasons",
    "wsud:sign": "Sign a Written Statement of Unauthorized Debit (customer-scoped)",
    "wsud:read": "View signed Written Statements of Unauthorized Debit across customers",
    "bulk_import:run": "Trigger an on-demand auto-import scan of dropped files",
    "ml_training_example:write": "Submit or retract known-fraud training examples for the ML model",
    "end_user_documentation:read": "View the End User documentation",
    "admin_documentation:read": "View the Bank Administrator documentation",
    # Deliberately NOT in CUSTOMER_SCOPE_MASKED_PERMISSIONS below -- unlike customer:manage
    # (which a customer-scoped session can never hold, regardless of what its security
    # group nominally contains), this one is meant to be held and exercised by a
    # customer's own scoped users. This is the first genuinely customer-self-service
    # permission in the catalog -- see web/routers/customer_banner.py.
    "customer_banner:manage": "Manage this customer's own banner message",
}

# Masked out of TenantContext.permissions whenever the active membership is
# customer-scoped (auth/deps.py::decode_and_build_context) — regardless of what the
# assigned security group nominally contains. These are all tenant-wide/bank-staff
# concerns (managing other users, other customers, the tenant's own branding/settings,
# admin ML controls, or the full cross-customer action log); a customer's own staff, or a
# bank employee acting in a customer-scoped context, must never be able to reach them even
# via a misconfigured security group.
CUSTOMER_SCOPE_MASKED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "user:manage", "security_group:manage", "tenant:manage", "customer:manage", "admin:manage", "audit_log:read",
        "ach_return_reason:manage", "wsud:read",
    }
)

# Deliberately excluded from _ALL/Admin's automatic grant — every OTHER permission in
# this catalog is included in "Admin" purely by construction (_ALL = every catalog key),
# but a full data export is a different class of risk (a single action that can move
# everything a tenant or customer has to an outside party), so even a tenant's own Admin
# group must be explicitly edited to add it via /ui/security-groups, rather than getting
# it "for free" like every other permission does today. This is a one-off exception, not
# a pattern to extend casually — most new permissions should still join Admin normally.
# wsud:sign joins it for the same reason: signing a legal unauthorized-debit attestation
# on the account holder's behalf is a distinct, deliberate-opt-in class of action, not
# something every Admin group should silently gain. ml_training_example:write joins for the
# same reason again: a bad or malicious label here silently shapes what the fraud model
# learns for every tenant, at least as consequential as the other two.
_NOT_ADMIN_DEFAULT = {"data_export:run", "wsud:sign", "ml_training_example:write"}

_ALL = [key for key in PERMISSION_CATALOG if key not in _NOT_ADMIN_DEFAULT]

# audit_log:read, wsud:read, and admin_documentation:read are deliberately excluded from
# the general read-permission bucket below — every other *:read key lands in
# Preparer/Approver/Viewer by default (they already see the underlying business records),
# but "who did what, when", "every customer's signed unauthorized-debit attestations", and
# the Bank Administrator documentation are all more sensitive/admin-facing than any single
# resource's own data, and default to Admin-only, same posture as the *:manage
# permissions. A tenant can still grant any of them to a custom group.
_ADMIN_ONLY_READS = ("audit_log:read", "wsud:read", "admin_documentation:read")
_READS = [key for key in _ALL if key.endswith(":read") and key not in _ADMIN_ONLY_READS]

# Default groups seeded into every new tenant (provisioning_service.py, tests'
# TenantFactory) — chosen to exactly reproduce the old Admin/Preparer/Approver/Viewer
# role behavior, so nothing changes out of the box.
DEFAULT_SECURITY_GROUPS: dict[str, list[str]] = {
    "Admin": _ALL,
    "Preparer": [
        "account:read",
        "issued_item:read", "issued_item:write",
        "stop_payment:read", "stop_payment:write",
        "paid_item:read", "paid_item:write",
        "check_image:read", "check_image:write",
        "ach_authorization:read", "ach_authorization:write",
        "ach_transaction:read", "ach_transaction:write",
        "exception:read", "exception:recommend",
        "wsud:sign", "bulk_import:run", "end_user_documentation:read",
    ],
    # end_user_documentation:read isn't listed explicitly here -- it's already included
    # via _READS (it isn't one of the _ADMIN_ONLY_READS above), same as every other
    # ordinary *:read permission.
    "Approver": [*_READS, "exception:decide"],
    "Viewer": [*_READS],
    # For an outside bookkeeper who services one or more customers (of this bank, and
    # possibly of other banks too, via their own separate memberships in those tenants —
    # see services/user_service.py::grant_multi_customer_access): every transactional
    # permission, but nothing management-related (no user/security-group/tenant/customer
    # management, no admin ML controls, no audit log, no data export). Deliberately
    # explicit (not computed from _ALL minus something) so a future new permission key
    # never lands here without a conscious decision, same as Preparer/Approver/Viewer.
    # Holding both exception:recommend and exception:decide doesn't bypass maker/checker
    # segregation — decision_service.decide() already blocks deciding one's own
    # recommendation whenever a tenant requires dual control.
    "Bookkeeper": [
        "account:read", "account:write",
        "issued_item:read", "issued_item:write",
        "stop_payment:read", "stop_payment:write",
        "paid_item:read", "paid_item:write",
        "check_image:read", "check_image:write",
        "ach_authorization:read", "ach_authorization:write",
        "ach_transaction:read", "ach_transaction:write",
        "exception:read", "exception:recommend", "exception:decide",
        "wsud:sign", "bulk_import:run", "end_user_documentation:read",
    ],
}
