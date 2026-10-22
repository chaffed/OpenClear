# OpenClear implementation runbook

This is a standalone version of the in-app "Getting Started" checklists
(`/ui/wizard/bank` and `/ui/customers/{id}/wizard`) — useful for reading or printing
outside a browser session, e.g. during a kickoff call with a bank's implementation team.
Each step below links to the real page in the app; nothing here needs to be done through
a separate "wizard" flow — the checklists just track which of these you've completed.

Two runbooks: **implementing a new bank** (a new OpenClear tenant) and **implementing a new
customer** (one of a bank's own business clients, with its own segregated data).

## Part 1 — Implementing a new bank

A tenant (and its first admin login) must already exist before any of this — that's a
one-time bootstrap step run by whoever is deploying OpenClear
(`scripts/launcher.py` / `services/provisioning_service.py::create_tenant_with_admin`),
not a self-service signup flow. Everything below assumes you can already log in.

### Prerequisites — gather before you start

- Your organization's display name, and (optionally) a logo file and an accent color
  (hex code) for branding.
- Whether you want to **require dual control** on pay/return decisions — a different
  person must make the final call than the one who recommended it. This is a policy
  decision, not a technical one; it can be changed later, but decide your default now.
- Whether the four default security groups (Admin, Preparer, Approver, Viewer) already
  match how your organization splits responsibilities, or whether you'll need custom
  groups with a different permission mix. See the full permission catalog at
  `/ui/security-groups`.
- The account numbers and names for every bank account OpenClear should monitor.
- Your initial staff list: each person's email and which security group they should
  start in.
- If you plan to use single sign-on (Okta or Azure AD): the identity provider's issuer
  URL, a client ID/secret for a registered OIDC application, which claim carries
  group/role membership (commonly `groups` or `roles`), and — critically — **the mapping
  from your own IdP groups to OpenClear security groups**, since federated login always
  requires at least one such mapping before anyone can sign in that way (see
  `API.md`/the SSO admin help text for why: IdP authentication alone is never treated as
  sufficient, current group membership always is).
- Whether you serve business clients directly with their own segregated data — if so,
  see Part 2 for each one.

### Steps

1. **Set your organization's branding** — `/ui/settings`. Display name, logo, favicon,
   accent color. Shows throughout the app and on your own login page before anyone signs
   in.
2. **Decide on dual control** — `/ui/settings`, "Require dual control" checkbox. Takes
   effect immediately for every exception decided from that point on, no re-login
   needed.
3. **Review your security groups** — `/ui/security-groups`. Keep the four defaults,
   edit their permissions, or create your own. A group's permissions apply to everyone
   in it immediately, not after their next login.
4. **Add your accounts** — `/ui/accounts/new`, or bulk-upload a CSV from
   `/ui/accounts/bulk` if you have more than a handful.
5. **Add your staff** — `/ui/users/new` (or `/ui/users/bulk`), assigning each person a
   security group.
6. **Set up single sign-on** (optional) — `/ui/settings/sso`. Add a connection, add at
   least one group mapping (mandatory — a connection with zero mappings never lets
   anyone in), and optionally require SSO instead of password login once it's confirmed
   working (guarded against locking yourself out: you can't require SSO for a scope with
   no active, mapped connection).
7. **Add your first customer** (optional, only if you serve business clients directly)
   — `/ui/customers/new`. Creating one takes you straight into that customer's own
   runbook — see Part 2.

## Part 2 — Implementing a new customer

A "customer" is one of a bank's own business clients with fully segregated data — their
own accounts, users, and (optionally) their own SSO — layered on top of the same tenant,
governed by `TenantMembership.customer_id`. Creating a customer (`/ui/customers/new`)
redirects straight into this checklist at `/ui/customers/{id}/wizard`.

### Prerequisites — gather before you start

- The customer's profile details: customer number, legal name, primary contact,
  tax ID, address, and any external/core-banking cross-reference ID you want to record.
- The account numbers and names for every account belonging to this customer.
- This customer's staff list (bank employees assigned to them, the customer's own
  employees, or both) and which security group each person should have.
- If this customer will use their own, independent SSO connection (separate from the
  bank's own): the same IdP details as in Part 1 — issuer URL, client ID/secret, groups
  claim name, and the group-to-security-group mapping.

### Steps

1. **Add this customer's accounts** — `/ui/accounts/new`, selecting this customer.
2. **Add this customer's users** — `/ui/users/new`, scoped to this customer. A person
   scoped to one customer only ever sees that customer's own data, everywhere in the
   app.
3. **Set up this customer's own single sign-on** (optional) —
   `/ui/customers/{id}/sso`. Entirely independent of the bank's own SSO setup; same
   mandatory-group-mapping rule as Part 1.
4. **Review ML scoring for this customer** (optional, informational) — the customer
   detail page's "ML scoring" card. Exceptions are scored with the bank-wide model by
   default; once this customer has enough of their own decision history, a
   customer-specific model can take over automatically (Auto mode) — no action is
   required unless you want to override that.

## After go-live

- **Data export**: if granted the `data_export:run` permission (deliberately *not*
  included in the default Admin group — see the Security Groups page), a bank or a
  single customer can generate a full, downloadable export of their own data and files
  at any time, for migrating away from OpenClear. `/ui/settings/data-export` (bank-wide) or
  `/ui/customers/{id}/data-export` (one customer).
- **Audit log**: every setup action above is recorded in the tamper-evident action log
  (`/ui/audit-log`, `audit_log:read` permission) — useful for confirming exactly who
  configured what, and when.

## Monitoring (for the service provider)

Scrape `GET /api/v1/platform/metrics` every minute with a platform API key that has the
`operations` scope (header `X-Api-Key`). It answers in the Prometheus text format. Alert on:

| Metric | Alert when | What it means |
|---|---|---|
| `openclear_decisions_unsent_past_cutoff` | above 0 for 5 minutes | Decisions past their cut-off aren't reaching the banks' cores. Check that the disposition scheduler is on and `decision_lock_job` is running (Controls → scheduled jobs). |
| `openclear_jobs{status="failed"}` | rises | A background job used every retry: an import, an export, a webhook. Retry it with `POST /api/v1/platform/jobs/{id}/retry` once the cause is fixed. |
| `openclear_decision_files_oldest_unacknowledged_seconds` | above a bank's own file schedule plus an hour | A core isn't taking its decision files. |
| `openclear_webhook_deliveries{status="failed"}` | rises | A bank's webhook endpoint stopped answering for a day. |
| `openclear_http_requests_total{status="5xx"}` | rising fast | Server errors; the access log has the request ids. |

Request counts are per process since it started; the rest is read from the database.

A single-process SQLite installation uses write-ahead logging and waits up to five seconds for a
write lock, so the in-process worker importing a file doesn't make web requests fail with
"database is locked".
