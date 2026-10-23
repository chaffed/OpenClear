# OpenClear Fix Plan

Source: [EVALUATION.md](../EVALUATION.md) (2026-09-22). Finding IDs (S0, F1, U2, …) refer to
that file. Each phase is one PR (or a small set of PRs) that ships and is tested on its
own. Work top to bottom. Tick boxes as you go so a lost session can resume here.

**Ground rules for every PR**
- Start by writing a failing regression test that reproduces the finding, then fix it.
- Run the full suite (`python -m pytest -q`, about 14 minutes) before merging.
- Schema changes go through an Alembic migration with a working `downgrade()`
  (`tests/test_migrations/` enforces this).
- Every security-relevant action gets an `audit_log_service.record_action` call, the
  same as existing routes.
- Update `SECURITY_REVIEW.md` / docs templates when behavior changes.

---

## Phase 0 — Land the in-flight work (about ½ hour)

- [x] Fix the failing `test_web_approvals.py::test_detail_page_shows_notice_instead_of_decide_form_for_own_recommendation`:
      put "a different approver must finalize it" on one line in
      `templates/exceptions/detail.html` (L84–85).
- [x] Full suite green (957 passed, 1 skipped) → commit the approvals queue work (`exceptions.py`,
      `approvals.html`, `detail.html`, `base.html`, `.gitignore`, test file).
      Done 2026-09-22 on branch `approvals-queue` (commits `9bb0adb`, `ae7868e`); not pushed.

## Phase 1 — Critical and high security fixes, small blast radius (1 PR, about 1 day)

Done 2026-09-22 on branch `phase-1-security-fixes`: `898451f` (S0), `a67d085` (S1),
`eee370a` (F2/U9/S7), plus error-page wording. Full suite: 969 passed, 1 skipped. Not pushed. Notes: forms other than accounts (customers, security
groups, issued items, ACH transactions) now get the app-wide 409 page on duplicates rather
than inline form errors. Inline errors for those are a nice-to-have for Phase 7.

### 1a. S0 — Customer-scoped data export
Files: `web/routers/data_export.py`, `tests/test_web/test_web_data_export.py`
- [x] Bank-wide routes (`/ui/settings/data-export*`): raise `WebForbidden` when
      `ctx.customer_id is not None`.
- [x] Per-customer routes (`/ui/customers/{customer_id}/data-export*`): when
      `ctx.customer_id is not None` and differs from the path `customer_id`, raise
      `WebNotFound` (don't reveal that the other customer exists).
- [x] Decision (2026-09-22): **customer users may export their own customer's data**, through
      the per-customer route only, as above. `data_export:run` stays unmasked.
- [x] Tests: customer-scoped user → 403 on bank-wide list/start/download; 404 on other
      customer's list/start/download; 200 on own customer's export (if allowed).
- [x] Same audit for the API: confirm no `/api/v1` export route exists (none found).

### 1b. S1 — Group reassignment takes effect immediately
Files: `auth/deps.py::decode_and_build_context`, `tests/test_auth/`, `tests/test_web/test_web_users.py`
- [x] After loading `membership`, resolve the group from `membership.security_group_id`
      (not the token claim) and put *that* id into `TenantContext.security_group_id`.
      This also fixes the WebAuthn verify/setup routes, which mint tokens from
      `ctx.security_group_id`.
- [x] Also verify `group.tenant_id == tenant_id` as a defensive check.
- [x] Tests: log in as Admin → `update_membership` to Viewer → next request to
      `/ui/users` is 403. Same over the API with a bearer token.

### 1c. F2 / U9 — No more bare 500s
Files: `main.py`, `web/routers/accounts.py`, `services/account_service.py`, `templates/error.html`
- [x] Add a catch-all `@app.exception_handler(Exception)` for `/ui/*` paths that logs the
      traceback and renders `error.html` (500, generic message, no stack trace). Leave
      `/api/*` returning JSON.
- [x] Account create: catch `IntegrityError` → re-render the form with "An account with
      that number / external ID already exists." Do the same for any other create form with
      unique constraints. Grep `UniqueConstraint` in `domain/` and check each form
      (customers, security groups, ACH return reasons, SSO connections, users).
- [x] S7 (same file): validate `customer_id` from the form with
      `customer_service.get_customer(db, ctx.tenant_id, …)`, and show a form error on a bad
      or foreign id. Reject malformed UUIDs cleanly.
- [x] Tests: duplicate account → 400/200 with error text, not 500; bogus `customer_id` →
      form error.

## Phase 2 — Password lifecycle (1 PR, about 1–2 days) — F7

Done 2026-09-22 on branch `phase-2-password-lifecycle` (`bccdd9c`, `500194c`). Full suite: 986 passed, 1 skipped. Not pushed.
Decisions made along the way (per the standing "go with the recommendation" rule):
- The temporary password is **shown once to the admin** (not emailed), on a no-store page,
  with a Copy button. Emailing a working password is weaker, and SMTP is optional anyway.
- Self-service change and reset are **also blocked in the demo organization** now, not
  in Phase 4, because otherwise this phase would have given public demo visitors a new way
  to lock each other out.
- A wrong *current* password on the change form counts toward the login lockout.
- No JSON API endpoint for changing a password. The API refuses sessions that must change
  their password and points them to the web app.
- Admins can't reset their own password from Users; they use Security like everyone else.

Files: `services/user_service.py`, `web/routers/security_settings.py` (self-service),
`web/routers/users.py` (admin), `api/v1/users.py`, templates, `auth/password_policy.py`
- [x] `user_service.change_password(session, user_id, current, new, policy)`: verify the
      current password, enforce the tenant/customer password policy (the existing
      `auth/password_policy.py`), rehash, reset `failed_login_attempts`/`locked_until`.
- [x] Self-service page under `/ui/security` ("Change password") with CSRF and audit log
      `user.password_change`; send a notification email ("your password was changed").
- [x] Admin reset (`user:manage`): sets a temporary password shown once (or emailed if SMTP
      is configured) and sets a new `User.must_change_password` flag (migration). Login
      redirects flagged users to the change-password page before anything else.
- [x] Hide/disable for SSO-only scopes (password login disabled) — nothing to change.
- [x] Cross-tenant caution: `User` is global (one account can have memberships in several
      tenants). Only allow an admin reset when the user has no *active* memberships in other
      tenants, or restrict it to platform staff. Otherwise one bank's admin could take over
      a Bookkeeper's access at another bank. **Decided (2026-09-22): block it.** Show
      "This user belongs to other organizations; they must change their own password."
      Add a test for it.
- [x] Update `docs/admin/authentication.html`.
- [ ] Bumps `token_version` — **moved to Phase 3** (it's listed there). Meanwhile: after an admin
      reset, the user's existing sessions are already quarantined, because every request checks
      `must_change_password`. After a *self-service* change, the user's other open sessions stay
      valid until Phase 3 lands.

## Phase 3 — Sessions: refresh, revocation, expiry UX (1 PR, about 2–3 days) — F1, U1, S2, S4

Done 2026-09-22 on branch `phase-3-sessions` (`918bc0f`, `213f288`, docs commit). Not pushed.
Full suite on the Phase 3 code: 986 passed, 1 skipped, plus 26 new session tests passing
(1,012 total). Three deliberate-breakage checks confirmed the new tests catch regressions in
the idle timeout, revocation, and maximum-length rules. Checked live in a browser: the
idle dialog appears on schedule and "Stay signed in" renews the session.

Decisions made along the way:
- **Logout ends only that session** (a new `revoked_session` table keyed by a per-login
  `sid`), not every device. "Sign out everywhere" is the separate `token_version` bump.
- **The idle timeout is enforced by the server**: the web UI can only renew a session
  whose access token hasn't expired (2-minute grace). Otherwise a refresh token could revive
  a session idle for hours. The API keeps standard refresh-after-expiry behavior.
- **Renewal never extends the maximum session length.** Near it, the dialog says to save
  work instead of offering "Stay signed in".
- **Organization switch** ends the old session (only once the switch completes, so
  abandoning a WebAuthn step doesn't sign the user out).
- Added a self-service **"Sign out other devices"** on Security, alongside the admin
  "Sign out everywhere" on Users. The admin version works even for users in other
  organizations (it grants nothing, and they can sign straight back in).
- **Found and fixed:** the CSRF cookie expired with the access token, so any form left
  open past 30 minutes failed on submit. It's now a browser-session cookie.
- **Found and fixed:** the API WebAuthn sign-in dropped `customer_id`, so customer-scoped
  users got the wrong membership (or none).
- **Found and fixed (docs):** "Data export timeout" was described as a retention period;
  it's a run-time limit.

Files: `web/security.py`, `web/deps.py`, new `web/routers/session.py`, `auth/security.py`,
`auth/deps.py`, `domain/user.py` + migration, `static/js/app.js`, `main.py`
- [x] **S2 — revocation:** add `User.token_version` (int, default 0; migration). Put a `tv`
      claim in every token; `decode_and_build_context` and API refresh reject a mismatch.
      Bump it on logout, password change/reset, user deactivation, and admin "sign out
      everywhere" (new button on the Users page). Old tokens with no `tv` count as 0, so no
      forced logout on deploy.
- [x] **F1 — web refresh:** add `POST /ui/auth/refresh` (path matches the existing
      `REFRESH_COOKIE_PATH`, CSRF by header). It validates the refresh cookie, re-resolves
      the membership (the same logic as `api/v1/auth.py::refresh`; extract a shared
      helper), and issues new access and refresh cookies.
- [x] `app.js` keep-alive: while the user is active (input or clicks since the last
      refresh), refresh silently **5 minutes** before access expiry. Expose the expiry to JS
      through a `<meta>` tag in `base.html`.
- [x] **U1:** a warning dialog **5 minutes** (decided 2026-09-22) before an *idle* session
      expires: "You'll be signed out in 5:00 — Stay signed in / Sign out now", with a live
      countdown. "Stay signed in" calls refresh.
- [x] Fallback for GET requests: when `get_web_context` sees an expired (not invalid) access
      token on a GET, redirect to `GET /ui/auth/resume?next=<path>`. The refresh cookie is
      path-scoped to `/ui/auth`, so only that route can see it. It refreshes and redirects
      back, or sends the user to login if the refresh fails. This is safe as a GET because it
      only re-issues the caller's own session and changes no data. Validate `next` with
      `safe_next_path`.
- [x] Fallback for POST requests: `WebAuthRequired` on a POST should use the `Referer`
      path, never the POST URL, as `next`.
- [x] Cookie `max_age` should use the tenant's override
      (`ctx.access_token_expire_minutes`), not the global setting.
- [x] Rename the settings labels (U7): "Idle sign-out after (minutes)" / "Maximum
      session length (minutes)". Validate that the maximum is greater than or equal to the
      idle timeout.
- [x] **S4:** middleware adds `Cache-Control: no-store` to `/ui/*` (except `/static`,
      `/ui/branding/*`) and `/api/*` responses.
- [x] Tests: refresh rotates cookies; refresh with a bumped `token_version` → 401; logout
      invalidates the copied access token; POST after expiry redirects to the referring
      page; `Cache-Control` header present.

## Phase 4 — Outbound requests, uploads, public demo (1–2 PRs, about 2 days) — S9, S8, S10, S3, S11

**Status: DONE 2026-09-23 on branch `phase-4-hardening`. Final full suite: 1,065 passed, 1 skipped. Merged to `main`; not pushed.** Progress log. The
newest entry is last. Each entry is committed, so resume from the last one:
- 4d started first (smallest). Then 4a → 4b → 4c.
- 4d DONE: compose Postgres bound to loopback.
- 4a DONE: new `auth/outbound_http.py` (URL check + a connect-time public-IP-only httpx
  transport that pins the vetted address, so DNS rebinding doesn't work). Used for OIDC
  discovery, JWKS, and token exchange, plus every endpoint the discovery document names.
  Save-time check on SSO forms (create and edit; the edit routes used to turn any
  ValueError into a 404). `oidc_allow_private_hosts` setting for local test IdPs,
  refused in production. OAuth `state` is bound in the signed cookie and checked on
  callback (S3). Login page errors are generic, with details in the server log. SSO tests now
  echo the real state. 90 SSO/auth tests pass.
- 4b DONE: logo/favicon type detected from the bytes with Pillow (PNG/JPEG/ICO only, no SVG);
  the declared Content-Type is ignored. Branding responses get `CSP: default-src 'none';
  sandbox` + nosniff (the security-headers middleware now keeps a route's own CSP).
  Legacy SVG logos stop being served. Bulk-upload downloads are always
  application/octet-stream. Old tests used fake image bytes and now use real tiny images
  (`tests/image_helpers.py`). 76 related tests pass.
- 4c DONE: central lock list in `web/demo_guard.py`, checked in web and API auth (one
  place to audit; a test checks every pattern still matches a real route). Hourly
  `demo_reset_job` (`demo_tenant_reset_interval_minutes`, default 60; the scheduler now
  also starts for it). **Found and fixed:** the demo reset never restored organization-level
  settings (banner and login messages, colors, dual control, password rules…), so a visitor's
  changes survived every reset. It now restores every Tenant column to a new demo's values.
  Demo notices on the demo's pages and its sign-in page. 34 demo tests pass.
- Docs DONE: README (SSO issuer rules, the local-testing setting, demo locks, hourly reset),
  admin demo docs, .env.example.
- Live check DONE: demo sign-in notice, in-app notice, and locked actions confirmed in a
  browser. **Found and fixed:** the lock page said "You don't have access to this" (it
  now says "Turned off in the demo"), and flash messages, the demo notice, and the tenant
  banner at the top of every page ran under the theme/help controls.
- Full suite before the final tweaks: 1,063 passed, 1 failed. The failure was the Phase 2 demo
  password test, which expected the form's own error but now gets the demo lock (403, the intended
  behavior); the test was updated and a service-level test added.

### 4a. S9 — SSRF-safe OIDC
Files: `auth/oidc_service.py`, `services/sso_service.py`, `config.py`
- [x] On save: require `https://` issuers. Reject IP-literal/`localhost` hosts.
- [x] On every fetch (discovery, JWKS, token): resolve the host and reject private,
      loopback, link-local, and multicast addresses. Disable redirects or re-validate
      each hop. A small `safe_http_client()` helper covers all three calls.
- [x] `config.oidc_allow_private_hosts: bool = False` (allowed only when
      `environment=development`) so local test IdPs still work.
- [x] Callback error page: show a generic "Single sign-on failed" and log the detail
      server-side.
- [x] **S3:** put the `state` value into the signed `sso_state` cookie and compare it on
      callback.

### 4b. S8 — Uploaded content served safely
Files: `services/tenant_service.py`, `web/routers/branding.py`, `web/routers/bulk_uploads.py`
- [x] Remove `image/svg+xml` from allowed logo types (or sanitize the SVG server-side; removal
      is simpler). Sniff the real type with Pillow at upload time, the same approach as
      `check_images.py::_image_media_type`, and store the *sniffed* type.
- [x] Branding responses: add `X-Content-Type-Options: nosniff` (already global) and
      `Content-Security-Policy: default-src 'none'; sandbox`.
- [x] Bulk-upload download: always `application/octet-stream` plus attachment.
- [x] Existing SVG logos: handled at serve time, with no migration and nothing deleted. A stored logo whose
      type isn't PNG/JPEG/ICO is treated as "no logo" (404, not shown) until re-uploaded.

### 4c. S10 — Demo guardrails
Files: new `web/demo_guard.py` dependency, routers listed below
- [x] `forbid_on_demo` dependency: raises `WebForbidden` with the message "Disabled in the public
      demo" when `ctx.tenant_id` is the demo tenant. Apply it to: SSO connection
      create/edit (bank and customer), password change/reset, user
      deactivate/membership edits for the seeded demo users, `require_webauthn` toggles,
      logo/favicon upload, session-timeout settings, data export, ML
      retrain/activate.
- [x] Banner/login messages: allow edits, but add an **hourly** scheduled demo reset
      (decided 2026-09-22) via the existing APScheduler, in addition to the idle reset.
      Show "This demo resets every hour" on the demo login page.
- [x] Show a "Demo mode — some settings are locked" notice in the base template for the
      demo tenant.

### 4d. S11
- [x] `docker-compose.yml`: `"127.0.0.1:5432:5432"`.

## Phase 5 — ML model choice per bank (2 PRs, about 4–5 days) — S6

**Status: DONE 2026-09-23 on branch `phase-5-ml-model-choice`, merged to `main`, not pushed. Full suite: 1,106 passed, 1 skipped.** Progress log
(newest last; each entry is committed, so resume from the last one):
- Decisions made at the start (standing "go with the recommendation" rule):
  - Platform operator = a platform API key with a new `shared_model` scope. Keys get
    scopes (existing keys keep usage-metering only), so a metering key can't touch the
    shared model.
  - Platform operator actions are **API-only** in this phase. There's no platform-level
    user login to hang a web page on, so building one is deferred.
  - The data-pooling disclosure text is a config setting. A placeholder does **not** block
    production startup (that would stop the live Fly demo). Instead, the UI marks it
    "placeholder, have counsel review" and startup logs a warning.
  - Every retrain (shared, bank, customer) compares challenger vs. current model on the
    **same** recent held-out decisions. Previously a new model's AUC was compared to the old
    model's stored AUC from a different holdout.
- Order: 5.1 schema → 5.2 registry/train/predict scoping → 5.3 switching + seed →
  5.4 fraud-example approval → 5.5 governance routes (tenant + platform) → 5.6 settings UI,
  consent, customer page → 5.7 docs/tests.
- 5.1 DONE (schema): `Tenant.ml_model_source` (SHARED default) + changed-at/by,
  `ml_private_switch_allowed_at` (set to +90 days for banks created from now on in
  `provisioning_service`; existing banks NULL = no lock), `ml_shared_consent_at/by`;
  `MlModel.tenant_id` (customer models backfilled from their customer); `ExceptionItem.
  shared_training_approved_at`; `PlatformApiKey.scopes` (existing keys = ["usage"]). New
  settings: `ml_new_bank_private_switch_lock_days`, `ml_bank_model_min_decisions`,
  `ml_shared_pool_disclosure_text`. Migration `a9c4e2f1d7b5` with a backfill test. An older
  migration test that read today's ORM after a partial upgrade now upgrades to head.
- 5.2 DONE (scoping): model slots in `ml/registry.py` (shared / bank / customer, with
  ownership-checked activation). `ml/train.py`: the shared model trains only on SHARED banks
  (and only platform-approved fraud examples); a bank model trains on its own bank; fair
  champion/challenger on the same recent holdout for every slot, plus the 200-decision floor
  for bank models; the reason is stored in `metrics_json["evaluation"]`. `ml/predict.py`
  precedence: customer model → bank's choice (a bank-only bank never falls back to shared).
  The `tenant_id` feature was removed (the shared model needs a retrain). `retrain_job` walks the
  shared model, then each bank-only bank, then customers. The customer ML page is relabeled
  ("Bank's model", plus a new "Scoring with now" column). 11 new tests; an older test now
  approves its fraud example before training the shared model.
- 5.3 DONE (switching): `services/tenant_ml_service.py` handles the 90-day lock (with a
  message giving the date), switching to bank-only (copies each network's active shared model,
  including the artifact file, into the bank's slot and activates it), switching back to shared
  (requires consent, records who/when), `record_shared_consent`, `clear_switch_lock`, and a
  counts-only `shared_model_summaries`. **Found and handled:** after a bank leaves, the shared
  model still contains its data, and the scheduled job wouldn't retrain because the decision
  count *drops*. `retrain_job` now retrains the shared model whenever a bank has left since it
  was activated, and activates the result regardless of the accuracy comparison
  (`force_activate_reason`), since removing the departed bank's data comes first. 12 new tests.
- 5.4 + 5.5 DONE (governance): platform keys have scopes (`require_platform_scope`; existing
  keys = usage only; `scripts/create_metering_api_key.py --scope shared_model`). New
  `api/v1/platform_ml.py` (operator only): list/retrain/activate the shared model, feature
  importance, a pending fraud-example review list plus approve (audited in the bank's log), and
  lifting a bank's 90-day lock (audited). Bank admins (web `/ui/admin` and API `/api/v1/admin/ml/*`)
  can retrain/activate only their own bank-only model, get 403 for the shared model, and see a
  counts-only shared summary. Retrain and activate are now audit-logged. **Found and fixed:**
  `GET /api/v1/admin/ml/models` listed every bank's models, and the admin page's training-data
  counts were totalled across all banks; both are now limited to the viewer's bank. Four older admin
  API tests were rewritten for a bank-only bank; 10 new tests.
- 5.6 DONE (bank UI): new Settings → Fraud scoring model page (`web/routers/tenant_ml.py`),
  with the current choice (who/when), a plain-language comparison, the disclosure (flagged while it's
  still placeholder text), acknowledge / switch-to-bank-only (confirm checkbox, or the lock date) /
  switch-back (consent checkbox), all audit-logged. A link card on Settings. A new bank setup
  checklist step, "Review how fraud scoring uses your data", auto-completes on acknowledgement or a
  switch. Locked in the demo. 7 new web tests.
- 5.7 DONE: README, API.md (bank admin changes and the new "Platform — shared model" section),
  admin ML docs, data dictionary, SECURITY_REVIEW (original global-model finding closed). Checked
  live in a browser: the Settings page, acknowledge, switch, the admin page in both modes, the
  customer ML page, and the checklist step. **Fixed:** the customer model history printed raw
  metrics. The full run's one failure was an older checklist test that didn't complete the new
  required step (updated).
- Deliberate side effect: existing banks have no consent recorded, so their setup checklist
  shows the new "Review how fraud scoring uses your data" step as not done until an admin
  acknowledges it. That is the intended prompt.
- Deferred: a platform-operator **web** page (needs a platform-level login first; the API
  covers everything today); blending seed and bank models by data volume.

**Decided (2026-09-22): support both, and let each bank choose.** A bank either joins the
**shared network model** (pools its decision data with other participating banks) or keeps
a **bank-only model** trained only on its own data. The shared model is run by the platform
operator, and a bank-only model is run by that bank's own admins.

### How scoring picks a model
The per-customer layer already exists (`MlScoringMode` AUTO/GLOBAL/CUSTOMER in
`domain/customer_ml_setting.py`, used by `ml/predict.py`). This adds a **bank layer**
between the customer model and the shared model:

```
customer model (per the customer's mode, as today)
  └─ else the bank's model source:
       SHARED  → shared network model
       PRIVATE → bank-only model (always exists: seeded from a copy of the shared model at switch time)
```
The customer mode `GLOBAL` keeps its DB value but its label changes to "Bank's model"
(meaning "ignore the customer-specific model and use whatever the bank uses"), so no
data migration is needed.

### PR 5a — Data model and scoring
- [x] Migration: `Tenant.ml_model_source` enum `shared|private`, plus
      `ml_source_changed_at`, `ml_source_changed_by_user_id` (consent record), and
      `ml_private_switch_allowed_at` (see the 90-day lock below).
- [x] Migration: `MlModel.tenant_id` nullable FK. `NULL` = shared network model; set =
      bank-only model; customer models also get their tenant_id filled in (backfill from
      `customer.tenant_id`). Unique "one active model" logic in `ml/registry.py` becomes
      per `(network_code, tenant_id, customer_id)`.
- [x] Defaults (decided 2026-09-22): **every bank starts on `shared`**. Existing banks keep
      what they have (today that's shared for all of them), and new banks get the shared
      model until they choose to switch.
- [x] **90-day lock for new banks** (decided): at tenant creation, set
      `ml_private_switch_allowed_at = created_at + 90 days`. Existing banks get `NULL` (no
      lock) in the migration. A bank can't switch to bank-only before that date; the
      Settings card shows "Available on <date>". A platform-operator override (audited) is
      recommended for exceptional cases. Switching back to shared is never locked.
- [x] **Consent at onboarding:** since new banks now join the pool by default, show and
      record the data-pooling disclosure during bank setup (tenant creation /
      `/ui/wizard/bank` step), not only on the Settings card.
- [x] **Seed-on-switch** (decided): switching to bank-only copies the currently active
      shared model (artifact file + metrics) into a new `MlModel` row owned by the bank
      (`tenant_id` set, `version` like `seed-from-shared-v7`, `metrics_json.seeded_from`
      = shared model id) and activates it immediately, so scoring never has a gap. The copy
      is a frozen snapshot: it doesn't receive later shared-model updates. Each later switch
      to bank-only seeds a fresh copy, and older bank models stay in history for rollback.
- [x] **Champion/challenger** ("continue from there"): logistic regression can't keep
      learning from a copied model without the original (other banks') data, so bank
      retrains train on the bank's own decisions only, and a new model is promoted only
      if (a) it has at least `ml_bank_model_min_decisions` (new config, default 200) and
      (b) it beats the active model on the bank's most recent held-out decisions (AUC,
      recall on returns). Otherwise the model is recorded as `trained, not promoted` with the
      comparison shown on the admin page, and bank admins can still promote manually.
      A later option is blending the seed and bank models, weighted by the bank's data volume.
- [x] `ml/registry.py::get_active_model_row(session, network, *, tenant_id=None, customer_id=None)`
      and `activate_model(..., expected_tenant_id=, expected_customer_id=)` — the ownership
      check is extended to the tenant.
- [x] `ml/train.py::_load_labeled_decisions`:
      - shared model → only decisions from tenants where `ml_model_source == shared`
        (joined through `ExceptionItem.tenant_id` → `Tenant`);
      - bank model → only that tenant's decisions;
      - customer model → unchanged.
      Retrain cooldown key becomes `(network, tenant_id, customer_id)`.
- [x] `ml/predict.py`: implement the precedence above.
- [x] Remove the raw `tenant_id` feature (`networks/check/features.py` L52,
      `networks/ach/features.py` L20). The bank-only model makes per-bank behavior
      unnecessary as a feature. Release note: the shared model needs a retrain.
- [x] `workers/tasks.py::retrain_job`: loop shared model → each private tenant's bank model
      → customer models (existing), using the same `_train_and_log` failure isolation.
- [x] Fraud-training examples (`ml_training_example:write`): from a private bank, they feed
      only its bank model. From a shared bank, they feed the shared model only once the platform
      operator approves them (`approved_for_shared` flag + review list), to block poisoning.
- [x] Tests: a private bank's decisions never appear in the shared training set; the
      precedence table (customer → bank → shared → none) for every combination; the
      `tenant_id` feature is gone; activation ownership checks.

### PR 5b — Governance, settings UI, platform operator
- [x] **Platform operator** for the shared model: retrain/activate/feature-importance
      move behind platform auth (extend `PlatformApiKey` / `platform_api_key_deps.py`). The
      platform-admin web page is **deferred** (API only, see the log above). A tenant admin only sees read-only "Shared model vN,
      active since …, trained on M decisions from K banks" (counts only, never other bank
      names).
- [x] **Bank admin** (`admin:manage`) gets full retrain/activate/rollback for its own
      bank-only model on `/ui/admin`, same as the existing per-customer page.
- [x] Settings → "Fraud scoring model" card (`tenant:manage`):
      - radio: *Shared network model* / *Bank-only model*, each with a plain-language
        description of the trade-off (the shared model starts useful sooner; bank-only keeps
        data in-house);
      - joining shared requires a confirmation checkbox with data-pooling disclosure text
        (a config-supplied setting, like the WSUD legal text, and startup refuses the
        placeholder in production);
      - the change is audit-logged (`tenant.ml_source_change`) and records who/when.
- [x] **Switching behavior** (shown in the UI before confirming):
      - shared → private (after the 90-day lock): the bank is immediately scored by its
        seeded copy of the shared model. Its data is excluded from the *next* shared
        retrain, and the platform operator is notified to retrain.
      - private → shared: scored by the shared model immediately; data joins the next
        shared retrain. The old bank-only models stay in history (can switch back).
- [x] Customer ML page: relabel the `GLOBAL` mode to "Bank's model" and show which
      model a customer is actually scored by right now.
- [x] Demo tenant (Phase 4c): lock this setting.
- [x] Docs: `docs/admin/ml-admin.html` + README; close the IOU in `SECURITY_REVIEW.md`.
- [x] Tests: switch blocked before day 90 and allowed after (freeze time); existing
      banks are never locked; the seeded model's scores equal the shared model's on the same
      input; a challenger with too little data or a worse AUC is not promoted;
      tenant admin → 403 on shared retrain/activate; bank admin can
      retrain/activate its own bank model but not another bank's; switching requires the
      confirmation checkbox and writes an audit entry; the tenant-facing shared-model
      summary never includes other tenants' identifiers.

## Phase 6 — Exception review UX (1–2 PRs, about 3 days) — U2, U3, U4

**Status: DONE 2026-09-23 on branch `phase-6-exception-review`, merged to `main`, not pushed. Full suite: 1,123 passed, 1 skipped (after fixing one older test that looked for the old link wording).** Progress log
(newest last; each entry committed):
- **Found at the start (High, UX):** `ml_score` is the model's probability the item should
  be **paid** (`ml/model.py`, and auto-disposition pays at ≥ 0.5), but the queue's help text
  calls it the "likelihood of return", and the detail page shows a bare "0.953". A reviewer
  could read that the opposite way round. Fix: show it as fraud risk (High/Medium/Low) with the
  estimated chance it should be returned (1 − score), everywhere it appears.
- Plan: 6.1 evidence service + labels/filters → 6.2 detail page (evidence, image, deadline,
  safe forms) → 6.3 queue (defaults, columns, labels) → 6.4 approvals follow-ups (FK +
  badge) → tests/docs.
- 6.1 DONE: `services/exception_evidence.py` (expected-vs-presented rows with mismatches flagged,
  plus stop payment, duplicate, void, and no-authorization notes, the check image, account/customer,
  and deadline, all through customer-scoped repositories). Label filters and `fraud_risk()` in
  `web/templates.py`.
- 6.2 DONE: detail page (facts header, evidence table, images, deadline in local time with
  what happens next, fraud risk wording). Safe forms: outcome starts on "Choose" (approve and revise
  start on the maker's choice), an empty outcome gets a clear message rather than a raw validation
  error, only the relevant reason field is shown, a High-risk Pay asks for confirmation, and with
  dual control off a decider sees only Decide.
- 6.3 DONE: the queue defaults to Needs attention (open + awaiting approval), oldest first;
  Account/Customer/Date/Decide-by columns; plain-English types and statuses; risk badges.
- 6.4 DONE: `recommended_ach_return_reason_id` FK (migration `b7d3e9a2c4f1`, with a text-match
  fallback for older rows); Approvals nav count badge (excludes your own recommendations).
- Tests: 17 new, two deliberate-breakage checks caught; two older assertions updated for the new
  wording. Checked live: the queue, and the check/ACH/stop detail pages. **Fixed after the live
  check:** confusing "couldn't be found" wording for stops on never-issued checks, a doubled period,
  and a duplicate-payment amount without "$".

Files: `templates/exceptions/detail.html`, `templates/exceptions/list.html`,
`web/routers/exceptions.py`, `web/templates.py` (label filter)
- [x] **U2 evidence panel:** a check exception shows an "Issued vs Presented" table (check #,
      amount, payee, date, account) with the mismatched fields highlighted, using
      `related_reference_id` → `IssuedItem`. An ACH exception shows the matched/closest
      authorization rule (max amount, SEC codes, company ID) and the transaction's values.
- [x] Inline check image (front/back thumbnails → full-size viewer) when one exists,
      plus links to the paid item and image record.
- [x] Apply the `currency` filter everywhere on the detail page. Add account and customer
      names.
- [x] Decision deadline: show when the default disposition will fire ("Auto-return at
      4:00 PM ET — 2h 13m left"), using `customer_disposition_setting` / the sweep job's
      logic.
- [x] **U3 safe forms:** outcome `<select>` starts on "— Choose —" and is `required`.
      Show only the reason input that matches the chosen outcome (small JS, with a
      server-side check already in `decision_service`). When dual control is off, and for
      a user with `exception:decide`, show only Decide (no stacked Recommend form).
      Add an optional confirmation for "Pay" when the ML score is high risk.
- [x] **U4 queue:** default the filter to Open + Pending approval. Sort oldest first / by
      deadline. Add Account, Customer, and Date columns. Human-readable exception type labels
      (`exception_type_label` filter: `amount_mismatch` → "Amount mismatch"). Hide the ML
      column when no model is active.
- [x] Approvals follow-ups (from the step 9 review): store `recommended_ach_return_reason_id`
      as a real FK (migration) instead of text matching; add a pending count badge on the
      "Approvals" nav item.
- [x] Tests: form requires an explicit outcome; the evidence panel renders issued and paid
      values; the default queue filter excludes decided items.

## Phase 7 — Layout and polish (1 PR, about 1–2 days) — U5, U6, U7, U8

**Status: DONE 2026-09-23 on branch `phase-7-polish`, merged to `main`, not pushed. Full suite: 1,128 passed, 1 skipped; phone overflow 0 of 31 pages; screenshots regenerated.** Progress log (newest
last; each entry committed):
- Scope: U5 mobile overflow, U6 dashboard, U7 settings, U8 link guard, plus the Phase 1
  carry-over (inline duplicate errors on the customer, security group, issued item, and ACH
  transaction forms), then screenshots.
- U5 DONE: root cause was `.page { margin: 0 auto }` on a column flex item on phones (auto
  margins stop it stretching, so the page grew to its widest table). Pinned to screen width. app.js
  wraps every table in `.table-scroll`. Measured at 390px: 15 of 31 main pages overflowed (up to
  689px) → 0 of 31.
- U6 DONE: the dashboard shows needs-attention, awaiting-my-approval, due-within-24h (highlighted),
  and oldest-waiting cards, each saying where it goes; the duplicate link list was removed. (The old
  cards *were* links; the evaluation was wrong about that, but nothing looked clickable.)
- U7/U8 DONE: color picker sized; auto-import no longer shows the server's absolute path; hidden
  markdown image input labeled; the Fraud Training link is shown only to people who can open it,
  and others are told which permission it needs.
- Carry-over DONE, and it was worse than recorded: seven forms showed **raw database error text**
  (table/column names and SQL) on failure. `web/form_errors.py::friendly_error` now gives plain
  messages; security groups show duplicates on the form.
- Screenshots regenerated (light and dark). **Caught in them and fixed:** my phone-layout rule
  (`.page > * { max-width: 100% }`) had overridden `.card-narrow`, stretching the Decide form full
  width; the dashboard's "Oldest" card text wrapped awkwardly.

- [x] **U5 mobile:** wrap every `<table>` in `.table-scroll { overflow-x:auto }` (put it in
      the shared table macro/`app.js` sortable helper so every page gets it). Toolbars and
      button rows get `flex-wrap: wrap`. Re-run the Playwright overflow check (the script
      logic is in the EVALUATION step 10 notes) and aim for 0px overflow on all 27 pages at
      390px.
- [x] **U6 dashboard:** make the stat cards links to filtered lists. Add "Awaiting my
      approval", "Oldest open exception", and "Due today". Drop the redundant link list.
- [x] **U7 settings:** fix the `input[type=color]` CSS (fixed width and height). Replace the
      absolute drop-directory path with the tenant-relative folder name. Label the two
      unlabeled inputs.
- [x] **U8:** wrap the Fraud Training link in `admin/ml_models.html` with
      `can(ctx, "ml_training_example:write")`, or add "(requires the Fraud Training
      permission)".
- [x] Regenerate screenshots (`scripts/generate_screenshots.py`).

## Phase 8 — Docs and operational readiness (about 1 day) — F5, F6, F4, F3, S5

**Status: COMPLETE (2026-09-23, branch `phase-8-docs-ops`, merged to `main`). Full suite: 1,138 passed, 1 skipped.** Progress log (newest
last; each entry committed):
- Order: F4 (X9.37 bundle control, code) → F6 (scheduler leader lock, code) → S5 + F6 docs →
  F5 architecture doc → final review of EVALUATION/FIX_PLAN.
- F4 done: `x937.py` validates Bundle Control (Type 70) item count and total amount against
  the checks since the preceding Bundle Header (Type 20), which resets the counters. It uses
  the standard layout (count at bytes 3–6, amount at 7–18), in line with the Type 90/99
  positions. Test fixtures now write real bundle totals. New tests: count mismatch, amount
  mismatch, a blank control rejected, and totals resetting between bundles. README,
  SECURITY_REVIEW and the X9.37 upload help text were stale (they said no control totals
  were checked) and are corrected. Still no real processor sample file in the repo: that
  part of F4 needs a file from a bank.
- F6 + S5 done: new `workers/leader_lock.py`. Every scheduled job is wrapped in
  `run_exclusively`: on Postgres it takes `pg_try_advisory_lock` on a stable per-job key and
  skips that tick if another instance holds it. The lock is released in `finally`, and the
  connection is discarded if the release fails. On SQLite/SQL Server the job just runs. The
  tests use a fake Postgres engine because no Postgres is available here (Docker is down), so
  the real advisory-lock SQL is still untested against a live server. README gains "Running
  more than one instance", which covers per-process rate limits (S5) and the scheduler rule
  (F6). It also corrects the old claim that demo idle-reset tracking was in-process: it uses
  `last_login_at` in the database. RUNBOOK is a bank-onboarding guide, so it's unchanged.
- Also added to the README multi-instance section: every file store (check images, logos,
  uploads, exports, ML artifacts, dropbox) is on local disk and needs a shared volume across
  instances. OCR and exports are in-process background tasks.
- F5 done: `docs/ARCHITECTURE.md` covers the big picture, tenancy and isolation layers, the
  data model (including why `source_item_id` has no FK), the network adapters, check/ACH
  rule order, exception → decision (maker/checker, auto-disposition), ML (slots, bank
  choice, champion/challenger), OCR, bulk ingestion, auth/sessions, UI vs. API, the export
  scoping table, background work, and known gaps. Each claim was checked against the code;
  first-draft errors about session timeouts, webhooks and the WebAuthn rationale were
  corrected. README (L11 and "Architecture at a glance") and all 12 code/test/migration
  "architecture plan" / "the plan's" references now point at it. The check-features
  docstring claimed historical features arrive "alongside Phase 5"; they were never built,
  so it now says so, and they're listed under Known gaps.
- Final review: EVALUATION.md now has an overall status line. S5/S6/S7/F3–F6 headings are
  marked (S6 and S7 were fixed in Phases 5 and 1 but had never been marked), and the
  approvals-queue notes are recorded as resolved in Phase 0.

- [x] **F5:** write `docs/ARCHITECTURE.md` (data model, network-adapter pattern, matching
      rules, ML pipeline, tenancy layers). Point the README and code comments at it.
- [x] **F6:** document "single process only" in the README/RUNBOOK, *or* add a leader lock
      (a Postgres advisory lock around each scheduled job; on SQLite, keep single-process).
- [x] **S5:** document that the rate limiter is per process. A shared store (for example
      Redis) is optional and only needed for multi-instance deployments.
- [x] **F4:** validate X9.37 Bundle Control (type 70). Get a real processor sample file and
      add it as a fixture.
- [x] **F3:** keep the Textract/Azure providers as roadmap items. No change is needed
      (startup already guards against them).

## Phase 9 — Verify model files before loading (added 2026-09-23) — SECURITY_REVIEW Low

**Status: COMPLETE (2026-09-23, branch `phase-9-artifact-integrity`, merged to `main`,
released as 1.5.0).** This wasn't in the original plan. It's the one item still open from
SECURITY_REVIEW.md, and it matters more now that the README recommends a shared volume for
model files.

- [x] `ml_model.artifact_sha256` (migration `c3e8f1a5d9b2` pins existing files; a row whose
      file is missing stays NULL and won't load until retrained).
- [x] `ArtifactStore.save` hashes the exact bytes it writes; `load_model` hashes and
      unpickles the same in-memory bytes, and refuses a mismatch or a missing hash.
- [x] Scoring skips a bad model (logged, item unscored); switching to bank-only never
      copies a bad shared model; the operator's feature-importance call returns 409.
- [x] Tests: `tests/test_ml/test_artifact_integrity.py` plus a migration test.
- Decision: a SHA-256 recorded in the database rather than an HMAC key. The database already
  decides which file loads, so a key would add management without adding protection.

---

## Decisions

| Phase | Question | Decision (2026-09-22) |
|-------|----------|-----------------------|
| 1a | Can customer users export their own data? | **Yes**, own customer only |
| 2 | Can one bank's admin reset a user who also belongs to other banks? | **No**, block it; the user self-serves |
| 3 | Idle warning / keep-alive interval | **Warn 5 min before timeout**; refresh on activity |
| 4c | Demo scheduled reset interval | **Hourly** |
| 5 | Shared vs. bank-only ML model | **Both, chosen per bank** |
| 5 · D1 | Default for existing / new banks | **Shared for all**; existing banks keep their current model source |
| 5 · D2 | Cold start for bank-only | **New banks locked out of switching for 90 days; switching seeds a copy of the shared model** (champion/challenger from there) |

## Estimated sequence

| Order | Phase | Size | Why here |
|-------|-------|------|----------|
| 0 | Land approvals work | XS | Unblocks a clean baseline |
| 1 | S0, S1, F2, S7 | S | Critical/high, tiny diffs, verified repros |
| 2 | Password lifecycle | M | High; Phase 3's revocation builds on it |
| 3 | Sessions | M | Biggest daily-use pain; completes S2 |
| 4 | SSRF, uploads, demo | M | Public demo is live exposure |
| 5 | ML model choice per bank | M–L | Done 2026-09-23 |
| 6 | Exception review UX | M–L | Done 2026-09-23 |
| 7 | Layout polish | S | Done 2026-09-23 |
| 8 | Docs/ops | S | Before any real multi-instance deployment |
