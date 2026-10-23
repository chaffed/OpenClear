# OpenClear Evaluation — Security, Incomplete Features, UI/UX

Started: 2026-09-22. Baseline commit: `db146c0` (plus uncommitted approvals-queue work).
Prior review: `SECURITY_REVIEW.md` (2026-07-28) — findings there are not repeated unless
still open or regressed.

**Status (2026-09-23): every finding below has been fixed, documented, or deliberately
left as a roadmap item (F3), in FIX_PLAN.md Phases 1–8. Phase 9 closed the last item from SECURITY_REVIEW.md (model
files are now verified before loading). All shipped in 1.5.0.** What's still open:
- no real processor X9.37 file to verify field positions (F4). **Largely resolved
  2026-10-09** by checking against Moov's open source X9 and ACH implementations and
  their sample files: that found and fixed real gaps (the X9.37 parser couldn't read the
  standard length-prefixed or EBCDIC formats at all, and had wrong positions for the cash
  letter date, the file total and the image record; the NACHA parser rejected most valid
  files over filler lines, addenda counts and general-ledger codes). What remains is a
  file from a bank's own processor, which a first implementation should still supply;
- Postgres-only code (RLS, the scheduler lock) has never run against a live Postgres here.

## Progress checklist

- [x] 1. Test suite baseline — 956 passed, 1 skipped, **1 failed** (13.5 min)
- [x] 2. Auth, sessions, CSRF, cookies (`auth/`, `web/security.py`, `web/deps.py`, `web/routers/auth.py`)
- [x] 3. Tenant/customer isolation (`db/tenancy.py`, `repositories/`, routers)
- [x] 4. Template XSS / `|safe` / Markdown rendering
- [x] 5. File uploads, bulk import, exports, path handling
- [x] 6. JSON API (`api/v1/`) authz
- [x] 7. Config, deployment (Dockerfile, fly.toml, demo tenant)
- [x] 8. Incomplete features (TODOs, stubs, dead links, unwired settings)
- [x] 9. Uncommitted approvals-queue work review
- [x] 10. UI/UX review (templates, navigation, forms, empty states, a11y)
- [x] 11. Final summary / prioritized list

## Prioritized fix list

| # | Item | Severity | Effort |
|---|------|----------|--------|
| 1 | S0 — customer-scoped `data_export:run` exports whole bank or other customers | Critical | Small |
| 2 | S1 — group demotion not enforced until token expiry | High | Small |
| 3 | S6 — any tenant admin controls the global ML model (still open from prior review) | High | Medium |
| 4 | U2/U3 — decision screen lacks issued-vs-presented evidence; forms default to Pay | High (UX) | Medium |
| 4b | F7 — no password change/reset at all | High | Medium |
| 5 | F1/U1 — web session refresh unbuilt: forced logout every 30 min, lost form work | Medium | Medium |
| 6 | S9/S10 — SSRF via OIDC issuer; public demo unrestricted | Medium | Small–Medium |
| 7 | S8 — SVG logos + echoed Content-Type (stored-XSS primitive) | Medium | Small |
| 8 | F2 — unhandled 500s (duplicate account, bad UUID) | Medium | Small |
| 9 | S2 — no server-side token revocation on logout | Medium | Medium |
| 10 | U4/U5 — queue defaults and mobile overflow | Medium (UX) | Small |
| 11 | S3, S4, S7, U6–U9, F3–F6 | Low / Info | Small each |

Suggested first PR: S0 + S1 + F2 (all small, each with a regression test). S0's and
S1's repros are described inline and are easy to turn into permanent tests.

## Findings

Severity: Critical / High / Medium / Low / Info.

### Security

**S0 — FIXED 2026-09-22 (Phase 1).** Critical (verified by repro) — A customer-scoped user with `data_export:run` can
export the whole bank's data or any other customer's data.**
`data_export:run` is not in `CUSTOMER_SCOPE_MASKED_PERMISSIONS`
(`auth/permissions.py` L62), and none of the routes in `web/routers/data_export.py` look
at `ctx.customer_id`: `/ui/settings/data-export/*` runs a tenant-wide export, and
`_get_customer_or_404` resolves `/ui/customers/{customer_id}/data-export/*` by tenant
only. Repro (throwaway test, since deleted): a user scoped to Customer A holding a group
with `data_export:run` got 200 on the bank-wide list, started + downloaded (200,
`application/zip`) Customer B's export, and started a full bank-wide export. Masking
exists precisely to survive a misconfigured group, so this defeats it. Fix: add
`data_export:run` to the masked set *or* (better, since customers may legitimately
export their own data) force `customer_id = ctx.customer_id` for scoped sessions and
403 the bank-wide routes when `ctx.customer_id is not None`. Add a regression test.

**S1 — FIXED 2026-09-22 (Phase 1).** High (verified by repro) — Changing a user's security group doesn't take effect until their access token expires.**
`auth/deps.py::decode_and_build_context` loads permissions from the token's
`security_group_id` claim (`db.get(SecurityGroup, security_group_id)`) and never compares
it to `membership.security_group_id`. Demoting an Admin to Viewer leaves them with Admin
permissions for up to 30 min (or the tenant's longer override). This contradicts the
documented "editing a group takes effect on the next request" guarantee (it only holds
for editing the group's *contents*, not reassigning the member). The WebAuthn verify
routes (`web/routers/auth.py` ~L207, ~L278) also mint fresh tokens from the
`mfa_pending` token's stale group id. Fix: in `decode_and_build_context`, use
`membership.security_group_id` (or reject when it differs from the claim).
Repro: Admin logged in → `update_membership` to Viewer → `/ui/users` still 200.

**S2 — FIXED 2026-09-22 (Phase 3).** Medium — Logout does not invalidate tokens server-side.
`/ui/logout` only clears cookies. A copied access/refresh token stays valid until
expiry (refresh: 7 days via the API `/api/v1/auth/refresh`). No `jti`/token-version
revocation exists. For a banking app, add a per-user `token_version` (bumped on logout,
password change, deactivation, group change) checked in `decode_and_build_context`.

**S3 — FIXED 2026-09-23 (Phase 4) — Low — OIDC `state` parameter is generated but never verified.**
`sso_start` passes `state=secrets.token_urlsafe(24)` to the IdP and discards it;
`sso_callback` ignores the returned `state`. The signed nonce cookie still blocks login
CSRF (id_token nonce must match), so this is defense-in-depth, but the OIDC spec
expects state binding. Store it in the `sso_state` JWT and compare on callback.

**S4 — FIXED 2026-09-22 (Phase 3).** Low — No `Cache-Control: no-store` on authenticated pages.
`web/security_headers.py` sets CSP/HSTS/etc. but not cache headers, so pages with
account numbers/amounts may be served from browser cache (back button after logout,
shared workstations). Add `Cache-Control: no-store` for `/ui/*` and `/api/*` responses.

**S5 — DOCUMENTED 2026-09-23 (Phase 8) — Info — Rate limiter is in-memory per process** (`web/rate_limit.py`). Fine for a
single instance; limits multiply with workers/instances. Account lockout (DB-backed)
covers password brute force. *Phase 8:* README "Running more than one instance" spells
out that limits multiply by process count, and what to do about it (divide the limit, or
enforce it at the proxy/WAF).

**S6 — FIXED 2026-09-23 (Phase 5) — High — Any tenant's admin controls the global ML model
that scores every tenant.** `MlModel` has no `tenant_id`; `api/v1/admin.py` (and the web
admin ML page) gate retrain/activate on per-tenant `admin:manage`, and fraud-training
examples (`ml_training_example:write`) feed that same shared model. One tenant can
retrain/roll back/poison the model used for all tenants' exception scoring, and
`feature_importance()` exposes other tenants' `tenant_id` values (features.py L52/L20).
Fix: restrict global-model actions to a platform-level role (the platform API key
concept already exists) and drop `tenant_id` as a raw feature. *Phase 5:* the shared
model is run only by the platform operator (`/api/v1/platform/ml/*`); each bank chooses
the shared model or its own bank-only model; shared training takes a bank's
fraud-training examples only after operator approval; `tenant_id` is no longer a feature.

**S7 — FIXED 2026-09-22 (Phase 1) — Low — Web account creation trusts a raw `customer_id` form field.**
`web/routers/accounts.py::create_account` (L68) does `uuid.UUID(customer_id)` without
checking the customer belongs to this tenant or is active. A tenant-wide user can
attach an account to another tenant's customer UUID (orphaned/inconsistent data, not a
read leak since repos filter by tenant). A malformed value is an unhandled 500. Validate
via `customer_service` like other routes do.

**S8 — FIXED 2026-09-23 (Phase 4) — Medium — Tenant-uploaded SVG logos are served publicly from the app's origin with a
client-supplied Content-Type.** `services/tenant_service.py` allows `image/svg+xml` and
stores the upload's declared content type; `/ui/branding/{slug}/logo` (unauthenticated)
serves it inline from the shared origin. CSP blocks inline script, but `script-src 'self'`
still allows same-origin script URLs — and `/ui/bulk-uploads/{id}/download` also echoes
the uploader's declared Content-Type. Together that's a stored-XSS primitive for a
tenant admin (e.g. SVG `<script href>` pointing at a bulk upload declared as
`text/javascript`) against users who open the logo URL. Fix: drop SVG (or sanitize and
serve with `Content-Security-Policy: sandbox` + `Content-Disposition: attachment`),
sniff types server-side as `check_images.py::_image_media_type` already does, and serve
bulk-upload downloads as `application/octet-stream`.

**S9 — FIXED 2026-09-23 (Phase 4) — Medium — SSRF via tenant-configured OIDC issuer.** `auth/oidc_service.py` fetches
`{issuer}/.well-known/openid-configuration` and then whatever `jwks_uri`/`token_endpoint`
that document names, with no scheme/host validation (`services/sso_service.py` only
strips a trailing `/`). `/ui/login/sso/{id}/start` is unauthenticated, so once a
connection exists anyone can trigger the fetch, and `OidcError` text is echoed on the
login page. A `tenant:manage`/`customer:manage` holder can point it at
`http://169.254.169.254/...` or internal services. Fix: require `https://`, resolve and
reject private/link-local/loopback addresses (and re-check on redirects), and show a
generic error.

**S10 — FIXED 2026-09-23 (Phase 4) — Medium (public demo only) — Demo tenant has no guardrails on destructive or
outward-facing admin actions.** With the published demo password (Fly deployment),
any visitor can: create SSO connections (S9 SSRF from the Fly host), change the demo
admin's password or deactivate users (locking others out until an *idle* reset — which
never comes while the attacker stays active), require WebAuthn on memberships, upload an
SVG logo (S8), set banner text/images every visitor sees, and retrain/activate models.
Fix: block these actions when `tenant.is_demo` (SSO, password/credential changes, user
deactivation, WebAuthn requirements, branding uploads), plus a periodic (not only idle)
reset.

**S11 — FIXED 2026-09-23 (Phase 4) — Info — `docker-compose.yml` publishes Postgres on `0.0.0.0:5432` with
`openclear/openclear`.** Documented as local-only; bind to `127.0.0.1:5432` to be safe on
shared networks.

What's solid (deployment): `environment` defaults to `production`; startup refuses
checked-in dev keys, the default SSO key, placeholder WSUD legal text, and stub OCR
providers; HSTS only over HTTPS; `/health` is minimal.

What's solid (isolation): `TenantScopedRepository`/`CustomerScopedRepository` are used
consistently; services resolve parent accounts through the caller's customer scope;
tenant-admin permissions are masked out of customer-scoped sessions; Postgres RLS as a
second layer; dedicated cross-tenant/cross-customer tests exist.

What's solid: ECDSA-signed JWTs with type claims, CSRF double-submit on every form,
strict nonce-based CSP, open-redirect guard, WebAuthn re-challenge on tenant switch,
lockout that also rejects the correct password while locked.

### Incomplete features

**F1 — FIXED 2026-09-22 (Phase 3).** Web session refresh is half-built. Login sets a `refresh_token` cookie scoped
to `/ui/auth` (`web/security.py::REFRESH_COOKIE_PATH`), but no `/ui/auth/*` route exists,
so the cookie is never used. Consequences: every web session hard-expires at the access
token lifetime (30 min default) even while the user is active; and the access cookie's
`max_age` uses the *global* setting, so a tenant's longer session-timeout override is
silently capped by the cookie expiring first (only shortening works).

**F2 — FIXED 2026-09-22 (Phase 1).** No database-constraint error handling anywhere. There is no `IntegrityError`
handling in the codebase. Unique constraints exist (e.g. `uq_account_tenant_number`,
`uq_account_tenant_external_id`), so entering a duplicate account number / external id
in the UI produces a generic 500 instead of a form error. **Verified live:** duplicate account number → bare "Internal Server Error" text page; `customer_id=not-a-uuid` → 500 (`ValueError`). Add a global handler that renders `error.html` for unhandled `/ui/*` exceptions, plus per-form validation.

**F3 — NO CHANGE (roadmap) — Cloud OCR providers are stubs.** `ocr/textract_provider.py` and
`ocr/azure_di_provider.py` raise `NotImplementedError`; only Tesseract works.
Correctly guarded (production startup refuses them), so this is a roadmap item, not a
trap.

**F4 — FIXED 2026-09-23 (Phase 8) — X9.37 Bundle Control (type 70) totals aren't validated** (cash-letter and file
controls are). Also, X9.37 field positions are self-described as "best-effort" and
untested against a real processor file. Get a real sample file before a bank relies on
it. *Phase 8:* Type 70 is validated against the checks since its Bundle Header (Type 20).
Still open: no real processor sample file to check field positions against.

**F5 — FIXED 2026-09-23 (Phase 8) — Referenced "architecture plan" doesn't exist in the repo.** README.md L11 and
code comments (`web/routers/auth.py` L166, `networks/check/rules.py` L21,
`networks/ach/features.py` L14, `ocr/tesseract_provider.py` L26) point readers to it
for rationale. Add it under `docs/` or remove the references. *Phase 8:* written as
`docs/ARCHITECTURE.md`; README and every code/test/migration reference now point at it.

**F6 — FIXED 2026-09-23 (Phase 8) — Background jobs assume a single process.** APScheduler runs in-process
(`workers/scheduler.py`) with no DB/advisory lock. The Dockerfile runs one uvicorn
worker, so it's fine today, but adding `--workers N` or a second Fly machine will run
retrain/dropbox-import/notification/disposition jobs N times (duplicate notifications
and imports). Document it or add a leader lock. *Phase 8:* on Postgres each job run takes
a per-job advisory lock (`workers/leader_lock.py`); SQLite/SQL Server stay single-process,
as documented in README "Running more than one instance" (which also covers shared file
storage).

**F7 — High — No way to change or reset a password.** — **FIXED 2026-09-22 (FIX_PLAN Phase 2).** Nothing in the codebase ever
updates `User.hashed_password` after the user is created. Users can't change their own
password, and admins can't reset one. The admin docs (`docs/admin/authentication.html`)
say there's no self-service reset and send users to an admin, but an admin can only
*unlock*. A user who forgets their password is stuck for good, a compromised password
can't be rotated, and a tightened password policy can never apply to existing users.
(Found while writing FIX_PLAN.md.)

Checked and fine: no dead `/ui/*` links or form actions in any template (every one was
resolved against the live router); every `config.py` setting is actually read;
email (SMTP) and SMS (Twilio) notification providers are real implementations.

### Uncommitted approvals-queue work (step 9)

Reviewed `git diff` + `exceptions/approvals.html` + `test_web_approvals.py`. Sound:
properly permission-gated (`exception:decide`), customer-scoped, oldest-first queue,
flags the viewer's own recommendations, pre-selects the maker's ACH return reason on
the detail page. Minor notes:
- `recommended_ach_return_reason_id` is recovered by matching `reason_text` back to the
  catalog. If a reason is renamed or two share text, pre-selection silently fails or
  picks the wrong one. Storing the FK on the exception would be more robust.
- The "Approvals" nav item has no pending-count badge, so checkers can't tell there's work
  waiting without clicking.
- **The new test fails:**
  `test_web_approvals.py::test_detail_page_shows_notice_instead_of_decide_form_for_own_recommendation`
  asserts `"a different approver must finalize it"`, but `exceptions/detail.html` L84–85
  wraps that sentence across a newline + indent, so the literal string never appears. This is
  a test/template text mismatch, not a logic bug. Put the phrase on one line (or normalize
  whitespace in the assertion) before committing.
- It renders one `load_source_item` query per row (N+1). That's bounded by page size, so
  it's acceptable.
- *Resolved in Phase 0:* the recommended ACH return reason is now stored as a foreign key
  (`exception_item.recommended_ach_return_reason_id`, with the text match kept only as a
  fallback for older rows); the Approvals nav item shows a pending count; the test was fixed
  before the work was committed.

### UI / UX

**U1 — FIXED 2026-09-22 (Phase 3).** Session expiry mid-form loses work. Because of F1, a reviewer who spends >30
min on an exception and then submits gets redirected to login; `WebAuthRequired` records
the POST path as `next`, so after re-login the browser GETs a POST-only URL (likely a
405) and the typed decision/notes are gone. Add refresh (F1), a pre-expiry warning, and
redirect POST-originated auth failures to the referring page instead.

Method for step 10: ran the app on a scratch SQLite DB with the demo tenant, then used
Playwright to log in as the demo admin and load 27 pages at 1366px and 390px widths.
Checked status codes, console errors, horizontal overflow, and unlabeled inputs, and
reviewed the screenshots by eye. Every page returned 200 with no JS console errors,
except the one 403 in U8.

**U2 — FIXED 2026-09-23 (Phase 6) — High — The exception decision screen lacks the evidence a reviewer needs.** This is
the core screen of positive pay (`exceptions/detail.html`):
- For a check `amount_mismatch` it shows only the presented amount (`939.00`). The
  issued amount, payee, issue date, and account aren't shown side by side, so the
  reviewer can't see what doesn't match without opening other pages.
- No check image inline (front/back), and no link to the paid item or image.
- For ACH `amount_exceeds_limit` it doesn't show the authorization rule's limit, SEC
  code, company ID, or receiving account.
- Amounts are unformatted on the detail page (`12000.00`) but formatted on the list
  (`$12,000.00`).
- No decision deadline or default-disposition time, even though an auto-disposition
  sweep exists.
Recommended: an "Issued vs Presented" comparison table with the mismatched fields
highlighted, plus a check image viewer.

**U3 — FIXED 2026-09-23 (Phase 6) — High — Decision forms default to "Pay".** Both the Recommend and Decide forms
preselect `Pay` with an optional reason, so one accidental click pays a possibly
fraudulent item. Use an empty "— choose —" option that must be selected, and consider a
confirmation step for Pay on items with fraud signals. For an admin who holds both
permissions, both forms appear stacked with identical fields, which is confusing. When
dual control is off, show only Decide. The ACH form has two reason inputs ("Return
reason" dropdown + "Reason code (used only for Pay)"). Show whichever one matches the
chosen outcome.

**U4 — FIXED 2026-09-23 (Phase 6) — Medium — The exceptions queue defaults to all statuses.** In the demo, 4 open items
were mixed in with 29 already-decided ones. Default to `Open` (and pending approval),
sort oldest-first or by deadline, and add Account/Customer and Date columns. Show exception
types as readable labels ("Amount mismatch"), not raw codes (`amount_mismatch`,
`amount_exceeds_limit`). The "ML score: no model yet" column is noise until a model
exists, so hide it when no model is active.

**U5 — FIXED 2026-09-23 (Phase 7) — Medium — Mobile layout overflows on most list pages.** At 390px, the whole page
scrolls sideways on Exceptions (+324px), Users (+409px), Audit log (+579px),
ACH authorizations (+355px), Accounts, Stop payments, Paid/Issued items, ACH
transactions, Settings, and Customer detail. The card is cut off at the right edge, and
button rows (e.g. Users: Add/Bulk/Look up/Export) run off-screen. Wrap tables in an
`overflow-x:auto` container (or stack them as cards on small screens) and let button
toolbars wrap.

**U6 — FIXED 2026-09-23 (Phase 7) — Low — The dashboard is thin.** The stat cards aren't clickable, and below them is a
plain list of links that repeats the sidebar. More useful: make the cards link to
filtered lists, and add "Awaiting my approval", "Oldest open exception age", and
"Decisions due today".

**U7 — FIXED 2026-09-23 (Phase 7) — Low — Settings page polish.**
- The accent-color `<input type=color>` is styled full-width and renders as a blank
  horizontal line, so it doesn't look like a color picker.
- The "Access token timeout" / "Refresh token timeout" labels are developer jargon.
  Say "Idle sign-out after (minutes)" and "Maximum session length". The web UI doesn't
  refresh tokens at all (F1).
- The Auto-import card shows the server's absolute filesystem path to tenant admins,
  which leaks internal server details. Show a relative/logical name.
- Two inputs on the page have no accessible label (flagged by the automated check).

**U8 — FIXED 2026-09-23 (Phase 7) — Low — A link leads to a 403 page.** `admin/ml_models.html` L39 links to
`/ui/ml-training/fraud-examples`, but Admin doesn't hold `ml_training_example:write` by
default (deliberately), so the default admin clicks it and gets 403. Wrap the link in
`can(ctx, ...)` or explain that the permission has to be granted.

**U9 — FIXED 2026-09-22 (Phase 1) — Low — Error pages are inconsistent.** 403/404 render the branded `error.html`, but
unhandled errors show a bare-text 500 (F2).

What's solid (UI): consistent layout and navigation, a skip link, a light/dark/system
theme, per-page help modals, "Getting started" wizards, pagination that preserves
filters, a sortable/filterable table helper, and clear empty states (e.g. "Nothing
waiting on approval right now").

## Notes for resuming

- All checklist items complete as of 2026-09-22. The fix work is planned in
  [FIX_PLAN.md](plans/FIX_PLAN.md) — track implementation progress there.
- Reproductions used throwaway files that have since been deleted. Nothing in the repo was
  modified except this file.
- Live UI check setup: a scratch SQLite DB with `OPENCLEAR_ENVIRONMENT=development`,
  `OPENCLEAR_DEMO_TENANT_ENABLED=true`, `OPENCLEAR_DEMO_TENANT_PASSWORD=...`; log in at
  `/ui/login/riverside-bank` as `admin@riversidebank.example.com`.
