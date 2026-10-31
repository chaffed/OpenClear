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
- **Who hears of an incident.** At least one incident contact (name, role, email, phone), and your
  CEO and CIO as the fallback, for Settings → Incident contacts. The service provider's incident
  and maintenance notices go to them (12 CFR 304.24). Confirm them once a year.
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
| `openclear_component_up{component=…}` | 0 for 2 minutes | That part of the instance isn't working: `database`, `storage` (a probe byte couldn't be written), `redis` (limits are being held per instance), or `scheduler` (the cut-off sweep hasn't run within its interval; check the scheduler and the leader lock). |

`GET /ready` answers the same per component, as JSON: `ready`, `degraded` (Redis or the sweep;
the instance still serves) or `unavailable` with a 503 (the database or storage). Point the
host's monitor at it; `GET /health` stays the load balancer's one-question check (the database).

Request counts are per process since it started; the rest is read from the database.

A single-process SQLite installation uses write-ahead logging and waits up to five seconds for a
write lock, so the in-process worker importing a file doesn't make web requests fail with
"database is locked".

## Backups (for the service provider)

Back up the database and every storage area together, as CUEC H-CP-9-01 asks, with the host's own
tooling (snapshots, `pg_dump`, bucket replication). When the job finishes, have it write a
manifest to the `backup_manifests` storage area (`OPENCLEAR_BACKUP_MANIFEST_STORAGE_DIR`, or the
`backup_manifests/` prefix of the bucket) as `latest.json`:

```json
{"completed_at": "2026-10-31T02:14:09+00:00",
 "database": {"bytes": 734003200, "sha256": "…"},
 "storage_areas": {"check_images": {"objects": 18240}, "bulk_uploads": {"objects": 912}, "decision_files": {"objects": 3310}},
 "kept_at": "s3://acme-backups/openclear/2026-10-31/", "retention_days": 35,
 "made_by": "backup.sh"}
```

The Controls page's *Backups fresh* (OC-CP-03) reads it: a warning when there's no manifest or
the last backup is older than `OPENCLEAR_BACKUP_MAX_AGE_HOURS` (26). A SQLite installation
needs no job: housekeeping makes a consistent copy nightly (`VACUUM INTO`) into the same area,
keeps `OPENCLEAR_SQLITE_BACKUP_KEEP_DAYS` (14) of them, and writes the manifest itself. Keep at
least one copy immutable or offline, behind a sign-in separate from production.

Prove a restore at least yearly with the drill, against a throwaway database (never the live one):

```
scripts/restore_drill.py /backups/openclear-2026-10-31.dump --scratch postgresql+psycopg://…/openclear_drill --manifest latest.json
scripts/restore_drill.py data/backups/sqlite/openclear-20261031T020000Z.sqlite --scratch sqlite:////tmp/drill.sqlite
```

It restores the dump (`pg_restore`, or a copy of the SQLite file), runs the migrations to head,
verifies every bank's action-log chain, reconciles the restored rows against file storage, checks
the dump's hash against the manifest, prints each step and writes a signed `restore_test` row to
the live database. Platform → Continuity lists the drills; the newest passed one is the "last
restore test" on every bank's Controls page, in place of `OPENCLEAR_LAST_RESTORE_TEST`.

## Recovering the installation (for the service provider)

In this order, because each step needs the one before it (CONTINUITY_PLAN.md BC-D7; rehearse it
against the sandbox at least yearly, and record the rehearsal):

1. **Tell the banks** from Platform → Incidents (an *Incident* notice: what happened, that
   decisions already made are safe, when you'll update them), or by the incident contacts' emails
   if OpenClear itself is down (Settings → Incident contacts lists them; the daily open-items file
   in each bank's storage, `data_exports/<bank>/continuity/`, is what a bank works from meanwhile).
2. **Start with the keys, not the database.** Nothing reads without them: the JWT key pair
   (`OPENCLEAR_JWT_PRIVATE_KEY_PATH`, `_PUBLIC_`), the file-signing pair
   (`OPENCLEAR_FILE_SIGNING_*`), the action-log signing pair (`OPENCLEAR_AUDIT_LOG_SIGNING_*`)
   and `OPENCLEAR_SSO_ENCRYPTION_KEY` (and `_PREVIOUS` if a rotation was under way), from the key
   store, exactly as they were. A different key verifies no signature and decrypts no SAML
   secret.
3. **Set `OPENCLEAR_MAINTENANCE_MODE=1`** on every instance before the database is reachable
   again: people can sign in and look, an administrator can take a data export, and nothing
   changes; the cut-off sweep pauses and the pause is treated as an outage across a cut-off when
   the mode ends (step 8), so nothing is decided by default for the gap.
4. **Restore the database** from the newest backup (`pg_restore --clean --if-exists`, or copy the
   SQLite file into place), then **`alembic upgrade head`**. If the backup predates the running
   release, the migrations bring it up; a backup from a newer release than the code is not
   restorable: deploy that release first.
5. **Restore file storage** to the same point as the database (the bucket's versioning point, or
   the directories' copy). The two are backed up apart; step 7 finds what disagrees.
6. **Start one instance** and check `GET /ready`: `database` and `storage` must be `ok`; `redis`
   may be `degraded` (limits are then per instance); `scheduler` says when the sweep last ran.
7. **Verify** before anyone decides anything: every bank's Controls page shows *Action log intact*
   (the chain verifies against the restored rows; a break means the backup and the log disagree
   and the bank must be told); Platform → Continuity → *Check now* reconciles rows and files and
   lists any row whose file is gone (an upload or image written after the backup: its bank should
   re-upload it, and the row is otherwise harmless); *Backups fresh* shows the backup you restored.
8. **Turn maintenance mode off** and start the other instances. The first cut-off sweep treats
   the pause as a gap: items due during it get the minimum review time again (never past a
   check's midnight deadline), the bank's administrators and the customers' deciders are
   emailed, and every bank's Controls page shows *No outage across a cut-off* failing for a
   week. Decisions a core had already taken before the backup are in its files; decisions made
   after the backup and before the outage are gone with the rows and must be made again, which
   the gap's notice asks for.
9. **Close the incident** with an *Update* or *Closed* notice, and record the recovery (the time
   it took, against the stated recovery objectives) in the restore drill's place: run
   `scripts/restore_drill.py` against the backup you used so the Controls page's last restore test
   is this one.

## Incidents (for the service provider)

The host of an installation is a bank service provider. When something disrupts the service, or
touches a bank's data, the banks must hear it from you, fast: their own clocks are 36 hours to the
FDIC and, for New York banks, 72 hours to DFS, and they start when the bank determines the
incident. The page [Incident notification](docs/compliance/incident-notification.md) has the rules.

**1. Decide whether it's a notice.** Send one when either is true:
- it has disrupted or degraded a bank's positive pay service, or is reasonably likely to, **for four
  hours or more** (12 CFR 304.24): pages, the API, imports, decisions reaching the core, the cut-off;
- it's a cybersecurity event that **directly affects a bank's systems or the data you hold for it**
  (23 NYCRR 500.11(b)(3)), including an attempt.

When in doubt, send it: a notice the bank didn't need costs it a minute, a missing one can cost it
a regulatory deadline. Record when you determined it; that's when your clock starts.

**2. Find the banks affected.** All of them for an installation-wide outage; otherwise use the
security events (`GET /api/v1/platform/security-events`), the operator's bank list and the metrics
above.

**3. Send the first notice within hours, not days.** Platform → Incidents, kind *Incident*: what
happened and when it started, what's affected, whether bank data is, what the bank should do now
(for example decide by hand before the cut-off, and keep its decisions ready for the core), and
when you'll update it. It's emailed at once to each bank's incident contacts (or its CEO and CIO),
and shows to its administrators until one acknowledges it. Platform → Incidents shows what you
sent; the bank's Settings → Incident contacts shows what it received and when it acknowledged it.

**4. Update, then close.** Send updates as kind *Update*, following the first notice, at the
times you promised and whenever something material changes; close with kind *Closed*. Follow
with the root cause within the time your contract says.

**5. Keep the records.** The notices, the acknowledgements, your timeline and the root cause,
for as long as your contracts and the banks' examiners need them.

**Scheduled maintenance** isn't an incident if you announce it beforehand (304.24(b)): send a
*Scheduled maintenance* notice to the banks it affects, before the window.

## Offboarding a bank (for the service provider)

When a bank leaves, its data is deleted and it gets a signed certificate (`plans/OFFBOARDING_PLAN.md`).
Everything is on Platform → the bank's **Offboarding**.

1. **Start offboarding**, when the contract ends. A second operator approves it under Changes. The
   bank is emailed (its incident contacts) and its administrators see a notice until one
   acknowledges it: its data goes after `OPENCLEAR_BANK_OFFBOARDING_WAIT_DAYS` (30), and it should
   take a final data export now. Nothing else changes: the bank keeps working until deletion.
2. **Wait, and check the export.** The page shows whether the bank has taken a full export since
   offboarding started. If it won't, get its refusal in writing: deleting without one needs the
   reason.
3. **Legal hold**, if a regulator or litigation needs the data kept: any operator puts it on, with
   the reason; taking it off needs two. Deletion is refused while it's on.
4. **Delete.** Type the bank's slug; the request shows exactly what goes (rows by table, files, the
   people who belong nowhere else). A second operator approves. It runs
   `OPENCLEAR_BANK_DELETION_DELAY_HOURS` (24) later, and **Stop offboarding** or a hold still
   stops it until then.
5. **Send the certificate.** Download it from the same page and send it to the bank's contacts with
   how long your backups keep copies. It's signed with the installation's file-signing key, so the
   bank can check it, and its action-log entry count and last hash let the bank check its export.

The bank's row stays, inactive, so its slug is never reused. The demo bank and sandboxes aren't
offboarded; they're wiped on their own schedule.

