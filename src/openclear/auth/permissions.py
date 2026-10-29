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
    # Custom-field values only, by file, drop folder or API (plans/CUSTOM_FIELDS_PLAN.md CF-D10), so
    # an automation's API client can keep officer codes current and change nothing else.
    "custom_field:write": "Set custom-field values on accounts and customers, by file or API",
    "issued_item:read": "View issued items",
    "issued_item:write": "Create/void issued items",
    "stop_payment:read": "View stop payments",
    "stop_payment:write": "Create/cancel stop payments",
    "paid_item:read": "View paid items",
    "paid_item:write": "Submit paid items",
    # A teller platform's only permission (TELLER_PLAN.md TL-D2): verify a check before cash is paid at a
    # branch, and record it cashed. It can't list issued items.
    "check:verify": "Verify checks cashed at a branch, and record them cashed (teller platforms)",
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
    "ach_return_reason:manage": "Manage the catalogs of ACH and check return reasons",
    "bulk_import:run": "Trigger an on-demand auto-import scan of dropped files",
    "end_user_documentation:read": "View the End User documentation",
    "admin_documentation:read": "View the Bank Administrator documentation",
    # Deliberately NOT in CUSTOMER_SCOPE_MASKED_PERMISSIONS below -- unlike customer:manage
    # (which a customer-scoped session can never hold, regardless of what its security
    # group nominally contains), this one is meant to be held and exercised by a
    # customer's own scoped users. This is the first genuinely customer-self-service
    # permission in the catalog -- see web/routers/customer_banner.py.
    "customer_banner:manage": "Manage this customer's own settings: its banner message and reminders",
    # Customers managing their own people and sign-on (CUSTOMER_ADMIN_PLAN.md). Held only
    # in a customer-scoped session, and only while the bank lets that customer manage
    # itself (Customer.self_manages_users / self_manages_sso; auth/deps.py).
    "customer_user:manage": "Manage this customer's own people: invite, change, deactivate (when the bank allows it)",
    "customer_sso:manage": "Manage this customer's own sign-on: its identity provider, email domains and SCIM (when the bank allows it)",
    "customer_api_client:manage": "Manage this customer's own API clients for its systems (when the bank allows it)",
    # Billing (BILLING_PLAN.md). billing:read is also meant for a customer's own scoped
    # users, who see only their own customer's statement; billing:manage is bank-only.
    "billing:read": "View billing statements and reports",
    "billing:manage": "Manage the fee schedule and customer billing terms, and finalize statements",
    # API clients (SDK_PLAN.md P0): credentials for the bank's own systems. Bank-only.
    "api_client:manage": "Manage integrations: API clients and webhooks for the bank's systems",
    # Control testing (CONTROLS_PLAN.md CT2): record a deviation's cause and response, and set the
    # grace period and which checks' warnings count as deviations. Bank-only for now.
    "compliance:manage": "Map CUECs to your own controls, set how deviations and attestations work, and record deviation responses",
    # CUECs and attestations (CONTROLS_PLAN.md CT4). Kept in a customer's own session, where they reach
    # only the customer's CUECs (its "responsibilities"). The bank's Admin group has all three.
    "compliance:read": "See the CUECs, their mappings and attestations",
    "compliance:attest": "Prepare, accept and reject CUEC attestations",
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
        "ach_return_reason:manage", "billing:manage", "api_client:manage", "check:verify",
        "custom_field:write",
    }
)

# Customer self-service permissions (CUSTOMER_ADMIN_PLAN.md): the opposite of the masked
# set above. They mean nothing to a bank-wide session, so they're dropped from one, and a
# customer-scoped session keeps each only while the bank lets that customer manage itself.
CUSTOMER_SELF_SERVICE_PERMISSIONS: dict[str, str] = {
    "customer_user:manage": "self_manages_users",
    "customer_sso:manage": "self_manages_sso",
    "customer_api_client:manage": "self_manages_api_clients",
}

# Deliberately excluded from _ALL/Admin's automatic grant — every OTHER permission in
# this catalog is included in "Admin" purely by construction (_ALL = every catalog key),
# but a full data export is a different class of risk (a single action that can move
# everything a tenant or customer has to an outside party), so even a tenant's own Admin
# group must be explicitly edited to add it via /ui/security-groups, rather than getting
# it "for free" like every other permission does today. check:verify is the second: it's a
# teller platform's, through its own API client, not something a person does. Not a pattern
# to extend casually — most new permissions should still join Admin normally.
_NOT_ADMIN_DEFAULT = {"data_export:run", "check:verify"}

_ALL = [key for key in PERMISSION_CATALOG if key not in _NOT_ADMIN_DEFAULT]

# audit_log:read and admin_documentation:read (and billing and compliance reads) are deliberately excluded from
# the general read-permission bucket below — every other *:read key lands in
# Preparer/Approver/Viewer by default (they already see the underlying business records),
# but "who did what, when" and the Bank Administrator documentation are all more sensitive/admin-facing than any single
# resource's own data, and default to Admin-only, same posture as the *:manage
# permissions. A tenant can still grant any of them to a custom group.
_ADMIN_ONLY_READS = ("audit_log:read", "admin_documentation:read", "billing:read", "compliance:read")
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
        "bulk_import:run", "end_user_documentation:read",
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
        "bulk_import:run", "end_user_documentation:read",
    ],
    # For a teller platform's API client (TELLER_PLAN.md TL-D2): verifying a check at a branch
    # and recording it cashed, and nothing else.
    "Teller platform": ["check:verify"],
}

# Bank-granted support access (UX_PLAN.md U8; services/support_access_service.py). A support
# person can read what a bank's own staff read, but never secrets, users, groups, settings
# or the action log; write access (approved by a second bank admin) adds entering and
# correcting items, never deciding them or anything about who may do what.
SUPPORT_READ_PERMISSIONS = frozenset(p for p in _READS if p not in ("billing:read",))
SUPPORT_WRITE_PERMISSIONS = frozenset({
    "account:write", "issued_item:write", "stop_payment:write", "paid_item:write", "check_image:write",
    "ach_authorization:write", "ach_transaction:write", "bulk_import:run",
})

