# Changelog

## 1.15.0 — 2026-10-13

Customers can manage their own people and sign-on, when their bank allows it
(CUSTOMER_ADMIN_PLAN.md).

- **The bank's switches**: on a customer's page, *Self-service* lets its own administrators
  manage its people, its sign-on, or both (also `PUT /api/v1/customers/{number}/self-service`).
  Security groups get *Offered to customers*: the only roles a customer can give. A new
  control, *Customer self-service*, lists who manages themselves and warns when one has no
  administrator. The customer's page links to its people and sign-on activity.
- **People** (Settings → People, `customer_user:manage`): invite by email or from a CSV,
  choosing role, accounts and passkey requirement; change, deactivate, reactivate; reset a
  password, unlock or sign out everywhere for people who belong to that customer alone; export;
  an activity log. Invitations create nothing until accepted: a new person chooses a password,
  someone who already uses OpenClear confirms with theirs (a wrong one counts toward their
  lockout). The last administrator can't be removed.
- **Sign-on** (Settings → Sign-on, `customer_sso:manage`): the customer's own OIDC or SAML
  connection, mapped only to offered roles; email domains verified by DNS TXT record, outside
  which neither sign-in nor SCIM creates anyone; SCIM tokens; *Test sign-in*; password sign-in
  turned off only from a session signed in through sign-on, and never leaving the customer
  with no way in. Customers can also tighten their own password rules.
- **Sessions from a customer's sign-on stay in that customer**: switching organization needs
  a fresh sign-in there.
- **Fixed**: under Postgres row-level security, the checks that stop a bank resetting the
  password of (or SCIM changing the email of) someone who also belongs to another bank saw
  only the bank's own memberships, so they let it through. They now count every bank's,
  through a narrow policy addition for one named person.
- **Migration** `c6e8a0c2e4f7`: `user_invitation`, `customer_email_domain`;
  `customer.self_manages_users`/`self_manages_sso`, `security_group.offered_to_customers`,
  `sso_connection.managed_by`; `notification.recipient_user_id` nullable; the
  tenant_membership policy's probe.

## 1.14.0 — 2026-10-13

What a bank's security and IT reviews ask for next: controls that alert, access by account,
API clients without shared secrets, and Azure storage.

- **A control changing status is a security event**: `control.status.changed`, when any
  control (Settings → Controls) turns WARN or FAIL, and when it recovers, with the old and
  new status. Recorded whenever the controls are read and checked hourly, so a SIEM can
  alert on a FAIL nobody was looking at (docs/guides/siem.md).
- **Per-account access**: the bank can limit one of a customer's people (or a customer's API
  client) to some of the customer's accounts (Users → Edit → Choose accounts, or
  `PUT /api/v1/users/{membership_id}/accounts`). They then see, change and are told about
  only those accounts' items, on every page and endpoint, and their bulk uploads and data
  exports cover only those accounts. The access review (`GET /api/v1/users`) shows it.
- **API clients can sign in with a key pair** (`private_key_jwt`, RFC 7523): register the
  client's public key, and it signs a short assertion with a private key that never leaves
  its systems; each assertion is accepted once. Its secret stops working (API.md). The
  "Integration secrets rotated" control no longer counts key-pair clients.
- **Azure Blob Storage** for files: `OPENCLEAR_STORAGE_BACKEND=azure`, with a connection
  string or a managed identity (`pip install 'openclear[azure]'`). Tested against Azurite.
  `scripts/move_files_to_object_storage.py` moves existing files there too.
- **Migration** `b5d7f9b1d3e6`: `control_status`, `client_assertion_use` and
  `membership_account`; `api_client.token_auth_method` and `public_key_pem`;
  `tenant_membership.all_accounts`.

## 1.13.0 — 2026-10-12

Reporting through each bank's own business intelligence platform: a guide, and what a data
pipeline needs from the API. It brings:

- `docs/guides/bi.md`: connecting Power BI, Tableau, Looker, Qlik or Superset through a warehouse;
- `updated_since` on every list, with cursor paging that never misses a row;
- read-only API clients, including a customer's own data feed;
- the full data export through the API;
- for the sandbox, the caller's real address behind Fly's proxy.

### For banks' data pipelines and BI platforms (docs/guides/bi.md)

- **`updated_since` on every list** (exceptions, issued and presented checks, stop payments,
  ACH filters and transactions, accounts, customers): only rows changed since a time, in the
  order they changed. Pages follow a `Link: rel="next"` cursor (`updated_since` with
  `after_id`), so a row that changes mid-way is never skipped. Each row now carries
  `updated_at` and `customer_id`.
- **Read-only API clients**: tick "Read only" when creating a client; whatever its group allows,
  it can only read (and run the full data export). Limited to one customer, it's that
  customer's own data feed.
- **The full data export through the API**: `POST /api/v1/data-exports` (the bank, or one
  customer with `customer_number`; a customer's client always its own), poll
  `GET /api/v1/data-exports/{id}`, download `file_url`.
- The BI guide covers all three.
- **Migration** `a4c6e8a0c2d5`: `updated_at` on exceptions, presented checks, ACH transactions
  and filters, stop payments, accounts and customers (from their creation time), indexes for
  changed-since reads, and `api_client.read_only`.

## 1.12.0 — 2026-10-12

Tools for the bank's staff and for whoever runs the installation, and for putting OpenClear
behind an edge network. It brings:

- the customer hub: customers with items to decide first, and "send a reminder now";
- platform staff, the operator view across banks, and setting up a bank from the web;
- support access granted by a bank, instead of impersonation;
- the edge check and the edge deployment guide for Cloudflare, Akamai and others.

### Bank staff and operator (MASTER_PLAN.md M6)

- **The customer hub.** The Customers list puts customers with items to decide first, with
  open items, items due today, the next deadline, the last issued-check file and the decision
  default at the deadline; search, and "No issue file in N days". A customer's page shows its
  open items and has **Send a reminder now**. The exceptions list filters by customer for bank
  staff (`?customer=`).
- **Platform staff.** People who run the installation (`scripts/platform_staff.py`, operator
  or support) sign in at `/ui/platform/login`, separately from any bank.
- **The operator view** (`/ui/platform`): every bank's customers, open and overdue
  exceptions, jobs, last import, controls and security-event delivery; setting up a bank from
  the web, with a first admin who sets their own password.
- **Support access instead of impersonation.** Settings → Support access grants a named
  support person access for a reason and a period (up to 72 hours), read-only unless a second
  admin approves write access, never to decide items or see secrets, users, groups, settings
  or the action log. They work under their own name, with a banner; ending the grant or its
  time running out ends the session at once. Every grant, approval, use and end is in the
  action log, as a security event, and in the evidence export.
- **Accessibility**: a table that scrolls sideways is now reachable by keyboard (focusable,
  and named, only while it overflows).
- **Migration** `f3b5d7f9b1c4`: `support_access_grant`, and the platform role on users.

### Edges (MASTER_PLAN.md M7)

- **The edge check**, `scripts/edge_check.py <url> [--origin <origin>] [--expect-ip <ip>]`:
  through Cloudflare, Akamai or a load balancer, it checks that OpenClear sees the caller's
  real address, the origin refuses requests that go around the edge, security headers arrive
  unaltered and nothing is injected, pages and the API aren't cached while static files are,
  responses are compressed, and legitimate requests (X9.37 and NACHA uploads, a large SAML
  response, SCIM, markdown with HTML-like text, URLs in settings, a burst of API calls) get
  through the WAF and bot management. It needs no account and changes nothing; `--json` for
  a report; exits 1 on a failure.
- **`GET /api/v1/edge/echo`**: how OpenClear saw the request (the caller's address and where
  it came from, whether the edge was trusted, the edge headers, the request id). No sign-in.
- **The edge deployment guide**, `docs/guides/edge.md`: origin lockdown, settings, WAF
  exceptions path by path, bot management, features to turn off, caching, sizes and timeouts,
  error pages, a go-live checklist, and notes for CloudFront, Azure Front Door and F5.
- `scripts/edge_ip_ranges.py cloudflare` prints Cloudflare's ranges for
  `OPENCLEAR_TRUSTED_PROXY_CIDRS`.
- Tested against a stub edge in the suite (`tests/stub_edge.py`) that behaves like Cloudflare
  and Akamai, including the mistakes real setups make; a real zone confirms it later.

## 1.11.0 — 2026-10-12

Settings you can see at a glance, reminders before the cutoff, security events delivered to
a bank's SIEM, and uploads in a customer's own format. It brings:

- one control panel for Settings and Admin, at bank and customer level;
- email and text reminders before each cutoff, with bank timing and a customer override;
- security events delivered to syslog, Splunk HEC or HTTPS, checked against a real Splunk
  and syslog-ng, with a control that fails when delivery falls behind;
- issue-file layouts built from a sample file, and failed rows fixed in the browser;
- drag and drop on every upload page, and fixes for pages that scrolled sideways on phones.

### Control panel, reminders and event shipping (MASTER_PLAN.md M4)

- **Settings is one control panel.** Settings and Admin are now one page of tiles, in five
  sections, each tile showing how its setting stands ("Dual control · On", "Controls · 1
  WARN"), with a search box. A person sees only the tiles their permissions allow.
  - Small settings open in a dialog with its own URL (`/ui/settings/branding`), so it can
    be bookmarked or linked; without JavaScript it's a plain page. Back closes it, focus
    returns to the tile, closing with unsaved changes asks first, saving updates the tile
    in place, and errors show in the dialog. It's full-screen on phones.
  - Lists and workflows (users, SSO, webhooks, controls…) open as pages with "Back to
    settings", which returns to their tile with the search kept.
  - A business customer's own users get their customer's panel: reminders, banner and
    billing statement.
  - `/ui/admin` redirects to the panel; the ML models page is now `/ui/admin/ml-models`.
- **Reminders before the cutoff.** Everyone who can recommend or decide an exception gets one
  message per reminder, "N items to decide, due in X", by email or text per their
  notification preferences. Timing per bank (Settings → Reminders; 2 hours and 30 minutes
  before by default, `OPENCLEAR_REMINDER_OFFSETS_MINUTES`), with a customer override.
- **Check return reasons** have their own settings page: add, edit, retire and restore,
  with their X9.100-188 codes. `ach_return_reason:manage` now covers both lists.
- **Security events delivered to a SIEM.** Syslog (RFC 5424 over TLS, as JSON, CEF or LEEF),
  Splunk HTTP Event Collector (with indexer acknowledgement) and HTTPS JSON. Each bank adds
  its own destination in Settings → Security event forwarding, with a test event; the
  operator can set one for the whole installation (`OPENCLEAR_SIEM_*`). Delivery is in
  order and at least once from a cursor, with backoff and catch-up, and never slows a
  request (`OPENCLEAR_ENABLE_SECURITY_EVENT_DELIVERY_SCHEDULER`). Events a destination
  hasn't received are kept past their retention until it has.
  TLS syslog is octet-counted (RFC 5425), plain TCP one message per line. Checked against
  real Splunk (HEC with acknowledgement, and its TCP syslog input) and a syslog-ng relay.
- **A new control, "Security events delivered"**: it fails when a destination has been
  failing for an hour or falls behind, and warns when there's no destination and nobody has
  read the pull API for a day. The evidence export adds the period's event counts by
  category and each destination's delivery record.
- **New security events**: `webhook.changed`, `users.exported`, `secrets.reencrypted` and
  `security_events.destination.changed`.
- **Docs**: a SIEM guide (`docs/guides/siem.md`) for Splunk, Sentinel, QRadar, Elastic and a
  syslog relay, with suggested detections; a log-management row in the FFIEC crosswalk.
- **Fixes**:
  - Sortable table headers are now real buttons inside the column header (axe flagged
    `aria-sort` on `role="button"`).
  - The scheduler now starts when only the decision outbox or control tests are turned on.
- **Migration** `d1f3b5d7f9a2`: reminder timing on banks and customers,
  `deadline_reminder`, and `security_event_destination`.

### Uploads (MASTER_PLAN.md M5)

- **Issue-file layouts.** A customer's issued-check files in their own format: delimited (any
  delimiter, with or without a header, columns by name or position), fixed-width (start and
  length per field, a record prefix, header and trailer lines skipped) or Excel. Amounts
  with a decimal point, implied cents or a separate cents column; named date formats; a void
  flag (a void line voids the check already issued); leading zeros dropped if asked.
  - Built from a sample in Settings → Issue-file layouts: choose what each column holds above
    the sample's first rows, or select a fixed-width field's characters; preview what would
    be imported, errors included, before saving. A short library of common shapes to start
    from. Bank-wide or per customer, with a default.
  - Used by the upload page, the API (`format=layout`, `layout_id`;
    `GET /api/v1/bulk-uploads/layouts`) and the drop folder (the default). Each upload keeps
    a copy of the layout it used.
  - A file whose columns don't match offers "Save these settings as a layout", starting from it.
- **Fixing failed rows in the browser.** Each failed row names the field at fault. "Fix
  failed rows" shows them editable, with the error beside the field; only the ticked rows are
  imported, each recorded as a correction with who made it, in the action log, and reversed
  by backing the upload out. The uploaded file never changes.
- **Drag and drop** onto every upload page's file input.
- **Fixes**: a wide table inside a form, or a visually hidden label in a table header, no
  longer widens the page on a phone.
- **Migration** `e2a4c6e8a0b3`: `import_layout`, `bulk_upload_correction`, and the failed
  field on row results.

## 1.10.0 — 2026-10-11

A release for running OpenClear in production, and for deciding faster. It brings:

- the real caller's address and per-person rate limits behind Cloudflare, Akamai or a load
  balancer;
- security events (sign-ins, failures, lockouts and more) with pull feeds for a bank's SIEM,
  and optional JSON logs;
- lists that search and export across every row, search everywhere, and clearer navigation;
- Pay and Return buttons, check return reasons, deciding several items at once, and "decide
  now" for a customer's people;
- branded pages for every error, and 503s that load balancers understand;
- long replays and evidence exports as background jobs.

### Request context and security events (MASTER_PLAN.md M1)

- **The caller's address behind an edge.**
  - `OPENCLEAR_CLIENT_IP_HEADER` and `OPENCLEAR_TRUSTED_PROXY_CIDRS` read Cloudflare's or
    Akamai's client-IP header, trusted only from the edge's ranges.
  - The address is worked out once per request, and used by rate limits, the action log,
    logs and security events.
- **Rate limits per person.** Signed-in people and API clients are limited as themselves
  (600 a minute by default); others by address.
- **Origin lockdown.** An optional secret header from the edge (`OPENCLEAR_EDGE_SECRET`),
  or a recorded attestation. There's a new control, "Edge network set up".
- **Request ids.**
  - `X-Request-ID` on every response: the edge's own id when trusted.
  - It's shown on error pages and recorded in logs and security events.
  - The action log records it, with the caller's address, in each entry's signed metadata.
- **JSON logs** (`OPENCLEAR_LOG_FORMAT=json`), with the request's context and an access line
  per request. Outbound HTTP URLs are no longer logged.
- **Security events.**
  - Recorded: sign-ins, failed sign-ins (with the reason), lockouts, second factors,
    tokens issued and refused, SSO failures, act-as and permission refusals, rate-limit
    refusals (sampled), and security-relevant changes from the action log.
  - Pull feeds: `GET /api/v1/security-events`, `GET /api/v1/audit-log` (with signatures),
    and `GET /api/v1/platform/security-events`.
  - The CLI follows them with `openclear events tail`.
- **Serving.**
  - A configurable keep-alive in the Docker image (`OPENCLEAR_KEEPALIVE_SECONDS`, 75).
  - Year-long caching for versioned static files.
  - A JSON `413` under `/api`.
  - The sandbox image no longer trusts forwarded headers from any address.

### Lists, search and shared components (M2: UX wave 0)

- **The main lists search the server, across every row.**
  - Issued items, paid items, ACH transactions and exceptions. Until now, the filter box
    searched only the rows on the page, so an item on another page looked as if it wasn't
    there.
  - Search by check or trace number, payee or originator, amount (exact or a range), date
    range and account number.
  - Sort any column across every row.
  - Export what's listed as CSV. Each export is in the action log, and is a security event.
  - Other paginated tables now say "Filter this page only".
- **Search everywhere** from the navigation: checks, ACH entries and exceptions, among what
  the person may see.
- **Navigation by work:**
  - Exceptions first, with a count of those needing attention.
  - "Action Log" throughout, where both "Audit Log" and "action log" were used.
- **Detail pages:**
  - Page titles name the item (e.g. "Check 1234 · $5,000.00 · Exception").
  - "Back" returns to the list you came from, with its filters.
- **Empty lists** say what to do next, with the button for it.
- **Shared components:**
  - a confirmation dialog in place of the browser's `confirm()`;
  - toasts for "saved";
  - submit buttons that can't be pressed twice while a form is being sent.
- **An accessibility check in the test suite.** axe-core in a browser checks ten main pages
  against WCAG 2.1 AA; the two problems it found (pager contrast) are fixed.

### Long requests as background jobs (M2: WAF wave B)

- **A replay** of a file over 1 MB, and **an evidence export** of more than 32 days, run in
  the background: `202` with a run to poll, then a download. Smaller ones answer at once as
  before. `openclear audit export` waits for the job.
- **Verifying the action log** runs in the background, and daily with the control tests.
  The Controls page shows the latest result and its age, instead of walking the whole log
  on every view.

### Deciding (M3: UX wave 1)

- **Pay and Return are two buttons.**
  - Paying needs no reason, and can take an optional note.
  - Returning asks why, from the bank's list for that kind of item.
  - Checks now have a list too: altered or fictitious, not authorized, stop payment,
    duplicate presentment, refer to maker, stale dated, signature irregular. Each has its
    X9.100-188 code, which goes with the decision to the core.
  - Paying a high-risk item still asks first.
- **Several at once.** Tick items on the exceptions list, then Pay or Return them together,
  with a confirmation of how many and how much. Each is decided, or recommended under dual
  control, exactly as from its own page; anything that can't be done is listed.
- **Decide now.** A customer's people with checks waiting land on them after signing in,
  and every page shows how many are waiting and the soonest deadline. It's amber within two
  hours and red within 30 minutes. The navigation's Dashboard link still opens the
  dashboard.
- **The exception page** leads with what differs, the decision and the check image. The
  rest is under "More about this exception". Keys: P to pay, R to return; J and K move
  through the list.
- **On phones** the list is cards, and the decision buttons are full width and stay in
  reach. The Pay and Return colours pass WCAG AA contrast.
- **Fixed:** "decide by" times read from SQLite showed hours out in the browser. They lacked
  a time zone, so the browser took them for local time.

### Error pages

- **Branded pages for every error on a web page.** Each has a message that fits it, a way
  back and a reference to quote to support. They replace raw JSON or plain text for:
  - an unknown address (404, with search);
  - a method a page doesn't take (405);
  - an expired form (403, "This form expired… reload the page");
  - a broken address or form (422);
  - a file over the size limit (413, stating the limit);
  - too many requests (429, with `Retry-After`).
- **The API answers JSON for all of them.** 413 and 429 were plain text.
- **A database that can't be reached is a 503 with `Retry-After`**, not a 500. `/health`
  checks the database and answers 503 when it doesn't answer.
- **Self-contained 502, 503 and 504 pages** (`static/errors/`) for a load balancer or edge
  to show when OpenClear can't answer at all.

### Upgrading from 1.9.0

- **Run `alembic upgrade head`.** It adds the `security_event` and `check_return_reason` tables, and gives every
  existing bank the default check return reasons.
- Behind Cloudflare or Akamai, set the client-IP header and ranges (README, "Reverse proxy /
  WAF deployment"). Until then, the new control fails, and everyone shares one rate-limit
  bucket per edge address, as before.

## 1.9.0 — 2026-10-09

A compliance release: evidence that OpenClear's controls work, for a bank's compliance
staff, auditors and examiners. It brings:

- segregation of duties, with two-person changes to rules and models;
- model risk tests: replaying past files through today's rules, and synthetic fraud
  capture;
- continuous control monitoring (Admin → Controls);
- a signed evidence export;
- AES-256-GCM for secrets stored in the database;
- crosswalks to FFIEC, Nacha, SR 11-7 and third-party risk guidance.

### Segregation of duties (COMPLIANCE_PLAN.md C1)

- **Conflicting duties.** Two rules apply to every bank:
  - whoever submits items (paid items, issued items, ACH, check images) can't decide exceptions;
  - whoever manages users can't also define what security groups grant.
- **Per transaction.** The person who presented an item can't decide its exception, even after
  moving to another group.
- **Warn or enforce**, chosen in Settings → Segregation of duties.
  - Every bank starts in **warn**: breaches are allowed and each one is logged as `sod.violation`.
  - **Enforce** refuses a breach with `403` and `ERR_SOD_VIOLATION`, and still logs it. It can only
    be turned on once no security group combines conflicting duties. The default Admin and
    Bookkeeper groups do, so they need splitting first.
- **Two-person changes** under enforce: turning a matching rule on or off, and activating an ML
  model, wait in Admin → Changes for a second admin. Nobody can approve their own change.

  The API's model activation follows the same rule: under enforce it answers `202` with a
  pending change.

### Model risk management (C2)

- **Replay** (Admin → Model risk, `POST /api/v1/mrm/replay`) runs a past X9.37 or NACHA file
  through today's rules in a dry run. It reports how they compare with what actually happened
  to each item: agreement, false positives, missed returns, unhandled items, and the items
  that differ.
- **Anomaly capture** (`POST /api/v1/mrm/anomaly-capture`) checks that the live rules catch
  synthetic fraud and refuse malformed files: a duplicate check, an altered amount, the wrong
  account, an ACH debit over its limit or from an unknown originator. It can run daily
  (`OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER`).
- Both are dry runs: nothing they create is kept except the report.

### Evidence export (C3)

- `GET /api/v1/audit/export?start&end&format=json|pdf`, Admin → Controls → Export evidence,
  and `openclear audit export` give a signed evidence bundle for a period. It covers: the
  system version; rules, plugins and models with their digests; security groups; whether the
  action log verifies; segregation-of-duties breaches; changes and approvals; every decision
  with who made it; model-risk runs; and the control status.

### Continuous control monitoring (C4)

- **Admin → Controls**, `GET /api/v1/controls/status` and `openclear controls` show each
  control's live status, PASS / WARN / FAIL, with evidence.
- **Secrets in the database are now AES-256-GCM** (they were Fernet, AES-128), with key ids.
  Existing values are re-encrypted at startup. Rotate the key by moving the old value to
  `OPENCLEAR_SSO_ENCRYPTION_KEY_PREVIOUS` and setting a new `OPENCLEAR_SSO_ENCRYPTION_KEY`.
- `OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION` records how the host encrypts disks and the
  database, which OpenClear can't see itself.

### Compliance crosswalks (C5)

- `docs/compliance/`: FFIEC IT Handbook, Nacha Operating Rules, SR 11-7 model risk, and
  2023 interagency third-party risk guidance, with a due-diligence pack. They're also on
  the docs site.

### Upgrading from 1.8.0

- **Run `alembic upgrade head`.** It adds `tenant.sod_mode`, and the `change_request` and
  `control_run` tables.
- Stored secrets move to AES-256-GCM on the first start. To keep a rollback to 1.8.0
  possible, back up the database first: 1.8.0 can't read the new format.

## 1.8.0 — 2026-10-09

An integration release: cores, online banking providers and identity systems can now
connect to OpenClear the way they expect, and others can extend it without changing it.
It brings:

- SDKs for Python, TypeScript and .NET;
- an API for a bank's systems (API clients, decision feed, webhooks, idempotency);
- SAML and SCIM;
- embedding in online banking;
- decision and NACHA return files;
- plugins;
- a developer sandbox with an integration checklist.

### Upgrading from 1.7.0

- **Run `alembic upgrade head`.** The Docker image does this on startup. It adds new tables
  for:
  - API clients;
  - idempotency keys;
  - decision feed cursors;
  - webhooks;
  - plugin choices;
  - SSO identities and SAML assertions;
  - SCIM;
  - embed launches;
  - the decision outbox;
  - conformance results.

  It also adds new columns on `sso_connection`, `tenant`, `api_client`, `decision` and
  `exception_item`, and existing decisions are numbered for the decision feed. Every new
  bank table is under row-level security on Postgres.
- **New dependency:** `python3-saml`. Its `xmlsec` dependency has prebuilt wheels, so no
  system packages are needed. Optional: `pip install 'openclear[docs]'` for the docs site.
- **Behaviour changes:**
  - Existing Admin groups get `api_client:manage`.
  - The default ACH return reasons get their NACHA codes where none was set.
  - An identity provider no longer attaches a new membership to an existing person who
    isn't already a member in that scope; add them as an admin first (Security fixes,
    below).
  - In the API contract, file downloads are declared as files.
- **Nothing new runs until you turn it on:**
  - plugins (`OPENCLEAR_PLUGINS`);
  - the decision outbox (`OPENCLEAR_ENABLE_DECISION_OUTBOX_SCHEDULER`);
  - sandbox sign-up (`OPENCLEAR_SANDBOX_SIGNUP_ENABLED`; never on an installation with
    real banks);
  - embedding (Settings → Embedding);
  - SAML and SCIM (per connection).
- **Downgrading to 1.7.0** is supported: `alembic downgrade b9d1f3a5c7e8`. Back up first.
  The new tables and columns are dropped, along with the data in them (API clients,
  webhooks, SSO identities, …). The default return reasons keep the codes they were given.

### Security fixes (review of this release's new features; SECURITY_REVIEW.md, 2026-10-09)

- **Account takeover by an identity provider:** sign-in, token exchange and SCIM no longer
  attach an identity provider's asserted email to an existing person who isn't already a
  member in that connection's scope. An admin adds such a person first. New people are
  provisioned as before.
- **Open redirect:** `next` paths with backslashes or control characters (`/\evil.example`)
  are refused.
- **Embedding origins:** these can no longer inject into the Content-Security-Policy
  header.
- **Sandbox sign-up:** it refuses to run on an installation with real banks.

### New: developer sandbox and integration checklist (SDK_PLAN.md P4)

- **Developer sandboxes:** on an installation run as a sandbox, a vendor gets a free,
  seeded sandbox bank at **/ui/sandbox**, with its admin sign-in and an API client. It's
  wiped after 30 days. The setting is `OPENCLEAR_SANDBOX_SIGNUP_ENABLED`; never turn it on
  where real banks are.
- **Integration checklist** (Admin → Integration checklist, `GET /api/v1/conformance`):
  shows what a core or online banking integration has done. A **webhook probe** checks that
  its receiver rejects a forged signature. See
  [docs/guides/certification.md](docs/guides/certification.md).
- **Test files:** `scripts/sandbox_test_files.py` builds issued-check, X9.37 and NACHA test
  files with Moov, from CheckSim's synthetic checks. `expected.csv` says what each item
  should become.
- **`sandbox/`:** the sandbox stack. It includes Apache Fineract and the Fineract adapter,
  which was checked against a real Fineract.
- **Docs site:** `python scripts/build_docs_site.py` builds a developer docs site from the
  repo's docs.
- **Docker image:** it now keeps decision files and audit archives under `/data`.
- **Upgrading:** run `alembic upgrade head`. It adds the tenant columns `is_sandbox`,
  `sandbox_expires_at` and `sandbox_contact_email`, and the table `conformance_result`.

### New: the `exception.deadline_approaching` webhook

- **What it does:** sent once per open exception when its decision deadline is near. The
  default is 4 hours before; change it with `OPENCLEAR_WEBHOOK_DEADLINE_WARNING_HOURS`.
- **When it's checked:** by the disposition sweep.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `exception_item.deadline_warned_at`.

### New: decision files for cores (SDK_PLAN.md P3)

- **Decision outbox:** turn it on under **Settings → Decision files**. OpenClear writes each
  run's new decisions to a file on the bank's schedule and sends the `decision_batch.ready`
  webhook.
- **Getting the files:** a core downloads and acknowledges each one at
  `/api/v1/decision-batches`.
- **NACHA return files:** the new `nacha_returns` layout writes the ACH debits the bank
  returned as a NACHA return file for its ACH operator. Set the bank's routing number and
  the operator's under Settings → Decision files. The files pass Moov's ACH validator.
- **Return reason codes:** the default ACH return reasons now carry their NACHA codes
  (R01, R02, R03, R08, R09, R10, R11). An existing bank's defaults get them too, unless
  the bank set its own.
- **For plugin authors:** decision layouts take the bank's settings:
  `write(feed, settings)`, with `config_fields`.
- **Scheduling:** set `OPENCLEAR_ENABLE_DECISION_OUTBOX_SCHEDULER=true`, or call
  `workers.tasks.decision_outbox_job` from cron.
- **Upgrading:** run `alembic upgrade head`. It adds the tables `decision_outbox` and
  `decision_batch`, and fills in return codes.
- **The `openclear` command** (in the Python SDK): `push` a file, `decisions pull`,
  `decisions files`, and `webhooks tail`, for scripts and cron.
- **An Apache Fineract adapter** (`integrations/fineract`). It:
  - syncs customers and accounts;
  - sends checks paid from savings accounts as presented items;
  - undoes a returned check's withdrawal in Fineract.
  It's a reference for other cores too.
- **A .NET SDK** (`sdks/dotnet`, `OpenClear.Sdk`). It's generated from the same contract
  and has the same retries, paging, errors and webhook verification as the Python and
  TypeScript SDKs.

### New: OpenClear inside online banking (SDK_PLAN.md P2)

- **Embedding:** a bank's business customers can review and decide exceptions inside the
  bank's online banking, without signing in again.
- **How a session starts:** the online banking server, acting for the signed-in person,
  asks `POST /api/v1/embed/launch` for a one-time link (good for a minute). Its page
  shows the link with the new **`@openclear/embed`** library (`sdks/embed`), or in a
  WebView in a mobile app.
- **What the embedded pages are like:**
  - no navigation;
  - framed only by the origins the bank lists under **Settings → Embedding**;
  - they tell the host page when they resize, navigate, record a decision or end.
- **Ordinary sessions** still can't be framed at all.
- **Guide:** [docs/guides/embedding.md](docs/guides/embedding.md). There's also a runnable
  host page in `examples/embed-host`.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `tenant.embed_allowed_origins` and the table `embed_launch`.

### New: online banking sign-on profile and token exchange (SDK_PLAN.md phase F)

- **A connection for an online banking provider:** the provider is the identity provider,
  and a **customer claim** names each person's business customer. Each session is
  limited to that customer.
- **Token exchange (RFC 8693):** the provider's server trades a signed-in person's ID token
  for an OpenClear token that acts for them. The token allows only what both the person
  and the API client may do, and the person is created on first use. Link the API client
  to the connection under Admin → API clients.
- **`OpenClear-Act-As-Subject`:** a client can act for someone by the provider's own id
  for them, instead of their email.
- **Upgrading:** run `alembic upgrade head`. It adds the columns
  `sso_connection.customer_claim` and `api_client.sso_connection_id`.

### New: sign-out from the identity provider, and sign-in from its portal (SDK_PLAN.md phase F)

- **OIDC back-channel logout:** when someone signs out at the identity provider, their
  OpenClear sessions end. The provider posts a signed logout token, which is verified
  and accepted only once.
- **Sign-in from a portal tile:** a tile in the identity provider's portal (Okta,
  Entra ID) can start OpenClear sign-in through the connection's "initiate login" URL.
  It runs the normal sign-in with PKCE, and only lands on pages on this site.
- **Where to find the URLs:** each connection's page lists them, along with the
  redirect URI.

### New: SCIM 2.0 provisioning (SDK_PLAN.md phase F)

- **What it does:** a bank's directory (Okta, Entra ID, ...) can create, update and
  deactivate people in OpenClear for an SSO connection, and push its groups, at
  `/scim/v2`.
  - People get access through the connection's existing group mappings.
  - Deactivating someone in the directory, or removing their last mapped group, takes
    their access away and ends their sessions.
  - Nothing is deleted; a deactivated person stays in the action log.
- **Turning it on:** on the connection's page. The page shows the bearer token once.
- **The PATCH formats Okta and Entra ID send** are both handled, including Entra's
  capitalised operations and `"False"` strings.
- **Upgrading:** run `alembic upgrade head`. It adds the tables `scim_user`, `scim_group`
  and `scim_group_member`, and the columns `sso_connection.scim_token_*`.

### New: SAML 2.0 sign-on (SDK_PLAN.md phase F)

- **SAML connections:** a bank, or one of its business customers, can sign in through a
  SAML 2.0 identity provider (ADFS, Okta, Entra ID, Ping, Shibboleth, ...) as well as OIDC.
  It uses the same group mapping, provisioning and "require SSO" rules.
- **Setting one up:** enter the identity provider's entity ID, sign-in URL and signing
  certificate. The connection page then shows OpenClear's entity ID, reply (ACS) URL and
  metadata URL for the identity provider.
- **Security:**
  - OpenClear signs its requests with a key pair generated for each connection.
  - Assertions must be signed with SHA-256 or better, addressed to OpenClear, and within
    their validity window.
  - Each assertion is accepted once.
  - An answer must match the request that this browser started.
- **Sign-in started at the identity provider:** for example, an OpenClear tile in Okta.
  It's off unless the bank turns it on for the connection.
- **Single logout:** a signed logout request from the identity provider ends the person's
  OpenClear sessions.
- **Upgrading:**
  - Run `alembic upgrade head`. It adds new SAML columns to `sso_connection`, makes
    `client_id` and `client_secret_encrypted` optional, and adds the tables `sso_identity`
    and `sso_assertion_seen`.
  - New dependency: `python3-saml`. Its `xmlsec` dependency has prebuilt wheels, so no
    system packages are needed.

### New: plugin matching rules, scoring features and notification channels (PLUGIN_PLAN.md PL3)

- **Matching rules:** a plugin can add checks to check and ACH matching, and each bank
  chooses which ones it uses under Admin → Plugins.
  - A rule can only **add** exceptions, never clear one.
  - A rule that fails flags the item ("A plugin's check failed (review by hand)") rather
    than letting it through.
- **Scoring features:** a plugin can add inputs to the fraud model. They apply to every
  bank, because the shared model is trained on all of them.
- **Notification channels:** a plugin can send a bank's events somewhere else, such as a
  chat tool. Each bank turns a channel on and fills in its settings; secrets are stored
  encrypted and never shown again. A channel gets the same events as webhooks, retried
  if sending fails.
- **Example and guide:** the example plugin has one of each, and
  [docs/PLUGINS.md](docs/PLUGINS.md) covers them.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `tenant_extension.config_encrypted`.

### New: Python and TypeScript SDKs (SDK_PLAN.md P1)

- **Two client libraries:** `sdks/python` (`openclear-sdk`, Python 3.11+, depends only on
  httpx) and `sdks/typescript` (`@openclear/sdk`, no runtime dependencies; runs on Node 20+,
  Deno, Bun and Workers). Both are licensed **Apache-2.0**, so vendors can ship them.
- **Generated from the API contract:** each SDK's models and endpoint methods are generated
  from `openapi/openclear-v1.json` by `scripts/generate_sdks.py`. A test fails when the
  generated code is out of date.
- **What the hand-written layer adds:**
  - client-credentials tokens, renewed automatically;
  - an `Idempotency-Key` on every write, reused across retries, which cover connection
    failures and responses 429 and 502–504, with `Retry-After` honoured;
  - page iterators;
  - bulk submit, which splits large lists, follows a `202` to its background import and
    returns results in the original order;
  - file upload;
  - decision feed consumption with acknowledgement;
  - Standard Webhooks verification;
  - acting for a person;
  - typed errors.
- **Tests:** both SDKs are tested against a running OpenClear (`tests/test_sdk/`).
- **The API contract now marks file downloads as files:** the CSV, XML and decision-file
  endpoints declare their real content type instead of JSON.

### New: plugins (PLUGIN_PLAN.md PL1)

- **What a plugin can add:** another payment network, OCR provider, email or SMS
  provider, storage backend, scoring model, or bulk import format. It's a separately
  installed package that declares an `openclear.plugins` entry point; OpenClear's code
  doesn't change.
- **Turning one on:** the operator allows it by name with `OPENCLEAR_PLUGINS`.
  Installing the package alone activates nothing.
- **When a plugin is skipped:** if it targets another plugin API version, fails to load,
  or claims a name that's already taken. The app still starts, and the problem shows
  under **Admin → Plugins** and in the log.
- **The built-ins work the same way:** they now register through the same interface.
  Nothing changes for existing installations.
- **Scoring model:** the model is chosen by `OPENCLEAR_ML_SCORING_MODEL`. It defaults
  to `logistic_regression`, so this doesn't change unless you set it.

### New: plugin import formats and decision files, per bank (PLUGIN_PLAN.md PL2)

- **Plugins can add decision file layouts:** the file a core takes back.
  `GET /api/v1/decisions/file?layout=` returns decisions in any layout the bank can use.
  The built-in `openclear_csv` is always available.
- **A bank chooses which plugin formats and layouts it uses:** a plugin's import formats
  and decision layouts are off for each bank until its admin turns them on under
  **Admin → Plugins**. The change is audit-logged.
- **Uploading a whole file through the API:** `POST /api/v1/bulk-uploads` (multipart)
  takes a file in any format the bank can use, built in or from a plugin.
  `GET /api/v1/bulk-uploads/formats` lists them. In the web UI, plugin formats are
  uploaded under **Bulk uploads → Other formats**.
- **`OPENCLEAR_PLUGINS_REQUIRED`:** OpenClear won't start if a plugin listed here
  doesn't load.
- **For plugin authors:**
  - [docs/PLUGINS.md](docs/PLUGINS.md) explains how to write a plugin, and
    `openclear.plugins.api` is the stable set of names a plugin imports;
  - `examples/openclear-plugin-example` is a working template, with a fixed-width
    import format and a fixed-width decision file.
- **Upgrading:** run `alembic upgrade head`. It adds the table `tenant_extension`.

### New: API clients for a bank's systems (SDK_PLAN.md P0)

- **OAuth 2.0 client credentials:** a bank admin creates an API client under Admin → API
  clients. It gets a security group (what it may do) and optionally a customer (whose
  data it may see). The system exchanges the client id and secret at
  `POST /api/v1/oauth/token` for a 30-minute token. Integrations no longer need a
  person's password.
- **A client acts through a service identity:**
  - it gets the same permission checks, customer scope, row-level security and audit
    trail as a member;
  - nobody can sign in as it, by password or SSO, and it can't be attached to by email;
  - it isn't listed or edited with the bank's people.
- **Rotating** a secret stops the old one at once; **revoking** a client stops it on its
  next request. `GET /api/v1/api-clients` lists clients for access reviews.
- **Upgrading:** run `alembic upgrade head` (new table `api_client`, new column
  `user.is_service_account`). Existing Admin groups get `api_client:manage`.

### New: a published API contract (SDK_PLAN.md P0)

- `openapi/openclear-v1.json` is the API's OpenAPI description, committed, and the base for
  generated SDKs (`python scripts/export_openapi.py` regenerates it).
- A test fails on any breaking change to `/api/v1`: a removed endpoint, a newly required
  input, or a removed response field.
- The web pages are no longer listed in the API description.

### New: customer and account sync (SDK_PLAN.md P0)

- `PUT /api/v1/customers/{customer_number}` and `PUT /api/v1/accounts/{account_number}`
  create or update by the bank's own numbers, so a core can sync nightly without lookups.
  Both have paged lists. Customer-scoped callers only touch their own accounts.

### New: webhooks (SDK_PLAN.md P0)

- **Events:** `exception.created`, `exception.decided` and `bulk_upload.completed`, sent to
  endpoints a bank sets up under Admin → Webhooks.
- **Standard Webhooks format:** signed with HMAC-SHA256, with `webhook-id` for
  de-duplication. Payloads carry only ids.
- **Delivery:** retried for about a day. Each endpoint's page shows recent deliveries, sends
  a test event, replays a failed delivery and rotates the signing secret.
- **Safety:**
  - events are written in the same transaction as the change that caused them;
  - URLs must be public https, checked again at connection time, with no redirects
    followed.
  - `OPENCLEAR_WEBHOOK_ALLOW_PRIVATE_HOSTS` is for single-bank installs with internal
    receivers.
- New tables `webhook_endpoint` and `webhook_delivery`. `api_client:manage` now covers
  webhooks too.

### New: the decision feed for cores (SDK_PLAN.md P0)

- **`GET /api/v1/decisions?after=N`:** every pay or return decision in order, with what a
  core needs to act on it: account, check or trace number, amount, date, outcome, return
  reason and ACH return code. Each decision gets the bank's next sequence number when
  it's made, and the numbers become visible in order, so a consumer never skips one.
- Also available as a CSV (`/feed.csv`).
- A consumer **acknowledges** what it processed (`POST /acknowledge`), and
  `GET /consumers` shows how far behind each one is.
- **Upgrading:** `alembic upgrade head` adds `decision.feed_sequence` and numbers existing
  decisions per bank in the order they were made (fraud-training examples excluded). It
  also adds `decision_feed_cursor`.

### New: an API client can act for a user (SDK_PLAN.md P0)

- With the bank's permission per client, a client sends `OpenClear-Act-As: <email>` to act
  for an existing member: an online banking provider acting for its signed-in customer.
- The request has only the permissions both hold. The audit log names the person, plus an
  `api_client.act_as` entry for the client.

### New: Idempotency-Key on API writes (SDK_PLAN.md P0)

- Send `Idempotency-Key` on a POST, PUT, PATCH or DELETE, and a retry gets the first
  response back (`Idempotency-Replayed: true`) instead of doing the work twice.
- The same key with a different request is refused (422). A retry while the first is
  still running gets 409. Server errors aren't kept, so a retry after one runs again.
- Keys are per caller and kept 24 hours (`OPENCLEAR_IDEMPOTENCY_KEY_HOURS`); housekeeping
  removes older ones. New table `idempotency_record`.

## 1.7.0 — 2026-10-09

A scale release: OpenClear can now import large files, train on large histories and run as
several instances, and it reads X9.37 cash letters and NACHA files as banks actually send
them.

### Upgrading from 1.6.0

- Run `alembic upgrade head` (the Docker image does this on startup). New:
  - `bulk_upload_file` status and progress columns;
  - the `bulk_upload_row_result` table;
  - the `audit_log_checkpoint` table.
- **API clients:** a `/bulk` call with more than 1,000 items now answers `202` with an
  upload to poll (`/api/v1/bulk-uploads/{id}`) instead of importing inside the request.
- **ML training** uses the last 365 days of decisions by default
  (`OPENCLEAR_ML_TRAINING_WINDOW_DAYS`; 0 for all history).
- **Nothing else changes until you turn it on:**
  - object storage (`OPENCLEAR_STORAGE_BACKEND=s3`);
  - shared rate limits (`OPENCLEAR_RATE_LIMIT_BACKEND=redis`);
  - housekeeping (`OPENCLEAR_ENABLE_HOUSEKEEPING_SCHEDULER=true`; once on, export
    archives expire after 30 days);
  - audit archiving (`OPENCLEAR_AUDIT_ARCHIVE_AFTER_DAYS`).
- New optional extras: `pip install 'openclear[s3]'`, `'openclear[redis]'`.
- Downgrading to 1.6.0 is supported (`alembic downgrade f7c9e1a3b5d6`); back up first.
  The bulk upload progress and row results are dropped. **If you turned on audit
  archiving, don't downgrade:** 1.6.0 doesn't know about archive checkpoints, so its
  "Verify chain" would report the archived entries as missing.

### Security and small fixes

- **Single sign-on uses PKCE** (S256) for OIDC connections, as current OAuth guidance
  asks even for a server-side client with a secret: an intercepted authorization code is
  useless without the verifier, which stays in the browser's signed sign-on cookie.
- The API documentation reported version 1.5.0; it now reads the installed version.

### Fixes: X9.37 and NACHA files as banks actually send them

Checked against Moov's open source X9 and ACH implementations (Apache-2.0) and their
sample files, which are now test fixtures:
- **X9.37 couldn't read a standard cash letter.** It now reads length-prefixed records
  (the standard) in EBCDIC or ASCII, as well as line-separated files. Fixed field
  positions:
  - the cash letter business date;
  - the 16-digit file total;
  - the Image View Data layout.
- **X9.37 account and check numbers:** the account number is taken from On-Us before the
  `/` symbol, without MICR dashes. The check number comes from Auxiliary On-Us or, failing
  that, after the symbol.
- **X9.37 return cash letters** are now rejected clearly instead of being skipped.
- **NACHA rejected most valid files.** It now:
  - skips the 9s filler lines;
  - counts addenda records in entry/addenda counts;
  - reads files without line breaks;
  - classifies general-ledger and loan transaction codes;
  - reads IAT account numbers;
  - falls back to the file date when the effective date is blank.

  ADV files are refused with a clear message.

### Scalability (SCALE_PLAN.md S4): bulk files import in the background

- **Uploads no longer import inside the web request.** Accounts, issued items, presented
  checks, ACH transactions and check images are stored, checked, and then imported by a
  background job. The upload's page shows progress, then the result: rows that failed
  and why, plus a CSV of every row. A file that can't be parsed is still rejected on the
  form with nothing imported. The dropbox import uses the same path.
- **Faster and safer imports:** 1,000 rows commit at a time, with each row in a savepoint,
  and audit entries are written a chunk at a time. On the test laptop a 50,000-row file
  now takes about 3.5 minutes instead of about 8. An interrupted import resumes where it
  stopped without importing any row twice.
- **One notification per upload:** reviewers get a single "N new exceptions" summary
  instead of one email or text per exception.
- **API:** a `/bulk` call with more than 1,000 items (`OPENCLEAR_BULK_API_SYNC_MAX_ROWS`)
  now answers `202` with an upload to poll at `/api/v1/bulk-uploads/{id}` (and
  `/results`). **Behavior change** for clients that send more than 1,000 items in one call.
- **Large files aren't held in memory:** uploads stream to disk, X9.37 cash letters are
  read through a memory map, and zip images are read as needed.
- **Fixes:**
  - the upload detail page failed for presented-check and fraud-training uploads;
  - a missing or unreadable ML model file made every exception row in an upload fail. It
    now leaves those exceptions unscored, as a failed integrity check already did.
- **Upgrading:** run `alembic upgrade head` (new columns on `bulk_upload_file`, new table
  `bulk_upload_row_result`).

### Scalability (SCALE_PLAN.md S7): data growth

- **Housekeeping:** with `OPENCLEAR_ENABLE_HOUSEKEEPING_SCHEDULER=true`, a daily run
  deletes finished background jobs (after 30 days), sent notifications (180), bulk-upload
  row results (365) and data export archives (30, then the export shows as expired). Each
  period is a setting; 0 keeps them forever. A bank's business records are never touched.
  **Behavior change** once enabled: export archives are no longer downloadable forever.
- **Audit log archiving** (opt-in, `OPENCLEAR_AUDIT_ARCHIVE_AFTER_DAYS`): older entries
  are verified and moved into signed archive files. The audit log page lists and verifies
  them, and they can be downloaded; full data exports include them.
- **CI checks query counts:** bulk imports must stay under 20 statements per row, and list
  pages mustn't make a query per row.
- **Upgrading:** run `alembic upgrade head` (new table `audit_log_checkpoint`).

### Scalability (SCALE_PLAN.md S6): several instances without a shared disk

- **Object storage:** set `OPENCLEAR_STORAGE_BACKEND=s3` and `OPENCLEAR_S3_BUCKET` (plus
  `OPENCLEAR_S3_ENDPOINT_URL` for MinIO, R2 and others) to keep check images, logos, bulk
  uploads, exports and ML models in an S3-compatible bucket. Install with
  `pip install 'openclear[s3]'`. Files already on disk stay readable;
  `scripts/move_files_to_object_storage.py` moves them over.
- **Shared rate limits:** `OPENCLEAR_RATE_LIMIT_BACKEND=redis` and
  `OPENCLEAR_RATE_LIMIT_REDIS_URL` count requests once across every instance. Install
  with `pip install 'openclear[redis]'`. If Redis is down, requests are allowed and the
  error is logged.
- **Connection pools:** `OPENCLEAR_DB_POOL_SIZE` (5), `OPENCLEAR_DB_MAX_OVERFLOW` (10),
  `OPENCLEAR_DB_POOL_TIMEOUT_SECONDS` and `OPENCLEAR_DB_POOL_RECYCLE_SECONDS`. The README
  covers sizing and PgBouncer.
- **Downloads stream:** large export archives and upload originals are no longer read
  into memory to send them.
- **Startup checks:** a production start refuses `s3` without a bucket, or `redis`
  without a URL.

### Scalability (SCALE_PLAN.md S5): ML training at scale

- **Training reads a window:** the last 365 days of decisions
  (`OPENCLEAR_ML_TRAINING_WINDOW_DAYS`, 0 for all history), at most the newest 250,000
  (`OPENCLEAR_ML_TRAINING_MAX_ROWS`). Known-fraud training examples are always included.
  **Behavior change:** decisions older than a year no longer train new models.
- **Less memory:** training reads only the two columns it needs, in batches. Loading
  13,000 decisions now peaks at 14 MB instead of 41 MB.
- **Each model records how it was trained** in `metrics_json["training"]`: rows used, rows
  eligible, whether it was capped, and how long loading and training took.
- **The nightly retrain counts in SQL** instead of loading every decision to count them.
- **Loaded models are capped per process** (`OPENCLEAR_ML_MODEL_CACHE_SIZE`, 256, least
  recently used dropped first).

## 1.6.0 — 2026-10-08

PosPay is now **OpenClear**. This release adds billing (account analysis and hard charge,
with camt.086 export), a durable background job queue, paged API lists, and Postgres
fixes for running at scale.

### Upgrading from 1.5.0

- Run `alembic upgrade head` (the Docker image does this on startup). On a large Postgres
  database, run it in a quiet window: the new indexes block writes to their tables while
  they build.
- **API clients:** list endpoints now return at most 500 rows per call (1,000 with
  `limit`). Follow the `Link` header to read the rest.
- Your `POSPAY_*` settings keep working; see the rename notes below.
- Downgrading to 1.5.0 is supported (`alembic downgrade c3e8f1a5d9b2`).

### Renamed: PosPay is now OpenClear

The project, its Python package (`openclear`), its settings (`OPENCLEAR_*`) and its launcher
(`run_openclear.command` / `.sh` / `.bat`) are renamed. **Existing installs keep working
without changes:**
- `POSPAY_*` settings are still read, from the environment and from `.env`. When both are
  set, `OPENCLEAR_*` wins. Rename them when convenient.
- A local `./pospay.db` is still used if there's no `./openclear.db`.
- The desktop launcher moves `.pospay-run/` to `.openclear-run/` on its first run (your
  database, keys and uploads come with it). It rebuilds its Python environment, which
  takes a minute.
- Platform API keys issued before the rename (`pospay_platform_...`) still work. New keys
  start `openclear_platform_`.
- A saved light/dark theme carries over.
- Kept as they are, because they identify existing deployments: the demo's Fly app and
  hostname (`pospay-demo`; WebAuthn keys are bound to it), its volume, and the Docker
  image's database path `/data/pospay.db`.
- If you import the package in your own scripts, change `pospay` to `openclear`.
- `docker-compose.yml` (development only) now names its Postgres user, database and
  volume `openclear`, so a local compose database starts fresh.

### Scalability (SCALE_PLAN.md S0, S1, S1a)

- **Postgres fixes:**
  - a fresh Postgres database can now be migrated;
  - authenticated API requests no longer fail on Postgres;
  - row-level security works end to end for a restricted app role. That covers login,
    requests, scheduled jobs, switching organization, usage metrics and creating a bank.
  - Connect the app as a role without `BYPASSRLS` (see README, "Postgres").
- **The audit log's hash chain is safe under concurrent writers:** a per-bank sequence
  number, a per-bank lock on Postgres, and a retry.
- **Tests:** the suite runs against Postgres in CI (`OPENCLEAR_TEST_DATABASE_URL`), and the
  CI failures on Linux / Python 3.11 are fixed.
- **Tools:** `scripts/perf/` has a volume generator and a timing script.
- **API list endpoints are paged** (S2): `limit` (default 500, at most 1,000) and
  `offset`, oldest first, with `X-Total-Count` and a `Link: rel="next"` header (see
  API.md). **Behavior change:** clients that read more than 500 rows from one call must
  now follow `Link`.
- **Faster hot paths** (S2): new indexes for the duplicate-paid check, stop-payment
  matching, the exceptions queue, the disposition sweep, the ACH frequency check and
  notification dispatch. The ACH frequency check counts in SQL. The queue pages load
  source items in one query per network, and customer exports filter decisions in SQL.
  On a large existing Postgres database, run the upgrade in a quiet window: building the
  indexes blocks writes to those tables while it runs.
- **Background jobs** (S3): OCR, data exports, dropbox scans and retraining are jobs in
  a durable queue (the `job` table) instead of work done inside the web request. They're
  retried with back-off, and picked up again if a worker dies. By default each web process
  runs a worker thread, so nothing changes for single-process installs. To size workers
  separately, set `OPENCLEAR_WORKER_MODE=external` and run `openclear-worker`. The platform
  operator can see and retry failed jobs at `/api/v1/platform/jobs`, with a platform key
  carrying the new `operations` scope.
- **Exports have no row limit** (S3): they stream to disk in batches. The
  `data_export_max_rows_per_entity` setting is gone, and the default time limit is now an
  hour instead of 5 minutes.
- **Over-long text** is now a form message (or a `400` from the API) instead of an error
  page on Postgres and SQL Server.

### New: billing (BILLING_PLAN.md)

- **A bank can bill its customers for positive pay.** The fee schedule draws on 24
  metrics: per customer, user, account, issued item or file, item evaluated, exception,
  item returned, stop payment, image, notification and more. Each fee has a unit price,
  optional step tiers, a monthly minimum and the bank's AFP Service Code. It's under
  Billing in the Organization menu and at `/api/v1/billing`.
- **Each customer is on account analysis, hard charge or exempt**, with a bank-wide
  default. The bank can also give a customer a discount, a negotiated price or a waiver
  on any fee.
- **Statements:** live for any period, with volumes per account, plus a PDF. Finalizing a
  month freezes it, and a replacement version can follow.
- **Exports** from a finalized month: the billing report (CSV), an analysis feed (CSV), a
  hard-charge posting file for the core (CSV), and ISO 20022 camt.086.
- **Customer users** with `billing:read` see their own statement.
- **Platform billing:** the platform operator can invoice each bank at
  `/api/v1/platform/billing`, with a platform key carrying the new `billing` scope.
- **Upgrading:** run `alembic upgrade head`. Existing Admin security groups get
  `billing:read` and `billing:manage`. Nothing is charged until a bank adds fees.

## 1.5.0 — 2026-09-23

A security, reliability and usability release. It fixes every finding from the September
2026 review ([EVALUATION.md](EVALUATION.md)). It also lets each bank choose which fraud
model scores its exceptions, and it adds new screens for deciding exceptions.

### Upgrading from 1.0.0

- Run `alembic upgrade head`. The Docker image does this on startup. The new migrations
  only add columns and fill in values for existing rows.
- **Existing banks stay on the shared fraud model**, with nothing to do. New banks start
  on it too, and can switch to a bank-only model after 90 days.
- **Model files are now fingerprinted.** The upgrade records each existing model file's
  SHA-256. A model whose file can't be found during the upgrade isn't loaded until it's
  retrained. Its exceptions go unscored in the meantime, and it's logged.
- **Several instances:** read "Running more than one instance" in the README first.
  Several instances against one database are supported on Postgres only, and they need a
  shared volume for files.
- The public demo organization now resets every hour, and some destructive actions are
  locked there.

### Security

- A customer-scoped user could export the whole bank's data. Exports are now limited to
  the caller's own customer.
- Changing a user's security group now takes effect on their next request, not when
  their token expires.
- Logging out, changing a password, and an admin's "sign out everywhere" now end sessions
  on the server.
- SSO issuer URLs can't reach private or internal addresses, and the OIDC `state` is
  verified.
- Uploaded SVG logos and bulk-upload downloads are served safely.
- Only the platform operator can train or activate the shared fraud model. Banks control
  only their own models. A bank's fraud-training examples reach the shared model only after
  the operator approves them.
- Model files are verified against their recorded SHA-256 before they're loaded.
- The development `docker-compose.yml` publishes Postgres on localhost only.

### New

- **Fraud model choice per bank:** each bank uses the shared model or its own model.
  Switching copies the shared model, so scoring never stops. New versions replace the
  active model only if they do at least as well on the same recent decisions. See
  Settings → Fraud scoring, and the platform operator API.
- **Exception review:** a detail page that shows the evidence (images, OCR, the matching
  issued item, which checks failed and why), with no default choice on the decision
  forms. The queue opens on items that need attention. There's also an Approvals queue for
  maker/checker review, with a count in the navigation.
- **Password lifecycle:** people can change their own password, admins can reset one, and
  a password change can be required at next login.
- **Sessions:** activity keeps a session alive, and the page warns 5 minutes before it
  would expire.
- A dashboard built around what needs doing.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how OpenClear fits together, and why.

### Fixes

- X9.37 files are checked against their bundle control totals as well as their cash
  letter and file totals. If any total doesn't match, the whole file is rejected.
- On Postgres, scheduled jobs run on one instance at a time.
- Errors show friendly pages and form messages instead of bare 500s or raw database text.
- Pages no longer scroll sideways on phones. The settings and admin pages were tidied.
