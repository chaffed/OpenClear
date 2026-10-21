# Full review plan: concurrency, security, features and the interface

A review of OpenClear 1.26.0 (`04d915d9`, 2026-10-21) on four axes: race conditions and data
integrity, security, what the product does and doesn't do, and the interface and its journeys.
Every finding below was checked by reading the code it names; "CONFIRMED" means the path was
traced end to end, "PLAUSIBLE" means the problem is there but its reach wasn't fully traced.
The earlier reviews (SECURITY_REVIEW.md, OWASP_PLAN.md's ASVS assessment, UI_PLAN.md's
evaluation, REVIEW_FIXES_PLAN.md) are not repeated: what they fixed was spot-checked and holds.

**The short version.** Nothing here is a tenant-isolation or authentication break: every
route checks a permission, every lookup is scoped, every form carries CSRF, and the earlier
fixes are in place. The real weaknesses are in the money path under concurrency, where state
transitions are check-then-act with no row locks (an item can be left undecidable; a check can
be paid twice; a decision file can carry the same decisions twice), and in a handful of
lifecycle states the code names but never sets. The interface's biggest costs are stale copy
from before cut-offs, lists without filters, and a sign-in that asks for a slug nobody knows.

## Findings

Severity: **High** = money or compliance impact with no mitigation, or a real security
weakening; **Medium** = wrong behaviour in a reachable case, or a security hardening a bank's
reviewer would ask for; **Low** = polish, or needs an unlikely precondition.

### A. Concurrency and data integrity

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| R1 | High | **Exception state transitions are check-then-act with no row lock.** `submit_recommendation` and `decide` each read `exception.status`, then write. Two people on one open item in a bank without dual control: if `decide` commits first and the recommendation second, the item ends `pending_approval` *with* a `decision` row; every later approval hits `uq decision.exception_item_id` and the item can never be decided. Two concurrent decides give the loser the generic "Conflicts with an existing record" 409 page, not "already decided". The same pattern is in `withdraw_recommendation`, `change_decision`, `recommend_change`, `undo_decision` and `auto_decide_exception`. | `services/decision_service.py:105-130, 157-205`; `services/auto_disposition_service.py:150-205`; `main.py::_handle_integrity_error` | CONFIRMED |
| R2 | High | **A check can be paid twice.** `_is_duplicate_paid` and `_find_candidate_issued_item` are plain selects; two presentments of the same check at once (the same paid-items file uploaded by two people, an API retry without `Idempotency-Key`, a dropbox file scanned twice) both see no duplicate, both match the issued item, both settle PAID. `paid_item` has no unique constraint to catch it. | `networks/check/adapter.py:93-115`; `networks/check/ingestion.py:44-95`; `domain/paid_item.py` | CONFIRMED |
| R3 | High | **The decision outbox can write the same decisions into two files.** `run` reads `max(to_sequence)` then writes a batch; the scheduler's `run_due` (leader-locked) and the API's `POST /api/v1/decision-batches` aren't locked against each other, and `decision_batch` has no unique constraint on `(tenant_id, from_sequence)`. A core that takes files processes those decisions twice. | `services/decision_outbox_service.py:100-135`; `api/v1/decision_batches.py:66-75`; `domain/decision_outbox.py` | CONFIRMED |
| R4 | Medium | **Undo or change can land after the cut-off lock.** `_changeable_decision` checks `locked_at is None and deadline > now` in Python; the sweep's `lock_due` locks, numbers and emits in another transaction. An undo read before the lock commits and committed after it deletes a decision the feed and webhook already carried (and frees its `feed_sequence` for the next decision, so a consumer's "everything after N" is wrong). The window is the cut-off second, but cut-offs are exactly when people act. | `services/decision_service.py:227-236, 300-314, 340-360` | CONFIRMED |
| R5 | Medium | **Two per-bank advisory locks are taken in both orders.** A web decide takes the feed lock (`assign_sequence`, inside `lock`) then the audit-chain lock (`record_action` in the route). An API client acting for a person writes its `api_client.act_as` audit entry *before* the route runs, so it takes the audit lock first, then the feed lock. Two such requests at one bank can deadlock; Postgres aborts one after `deadlock_timeout`, which surfaces as a 500. | `auth/deps.py::_acting_context`; `services/audit_log_service.py:104`; `services/decision_feed_service.py:41` | CONFIRMED |
| R6 | Medium | **Change requests can be approved twice, or approved and withdrawn.** `decide` and `withdraw` check `status == pending` then write, with no row lock: two approvers at once apply the change twice (two audit entries, two webhooks); approve and withdraw at once can apply a change that ends marked withdrawn. `settle`'s cooling-off apply races the same way. | `services/change_request_service.py:218-262, 265-274, 288-312` | CONFIRMED |
| R7 | Medium | **Decisions go out up to five minutes after the cut-off, all or none.** `lock_due` runs only inside the disposition sweep (`disposition_sweep_interval_seconds`, 300 s), after the auto-decide loop, in one transaction with one commit: one row's failure rolls back every bank's locks until the next sweep. The pages say "At the cut-off it becomes final and goes to the bank", and a core's own cut-off may not allow the lag. | `workers/tasks.py::sweep_expired_dispositions_job` (the final block); `services/decision_service.py::lock_due` | CONFIRMED |
| R8 | Medium | **A stale-looking job can run twice at once.** `requeue_stale` requeues a RUNNING job whose heartbeat is 300 s old even though its handler may still be running (a database stall, a GC pause). A second worker then imports the same upload from `processed_rows`: issued items are saved by their unique constraint, paid items aren't (R2). | `workers/jobs.py::requeue_stale, _execute`; `services/bulk_import_service.py::_import` | CONFIRMED |
| R9 | Low | **SQLite runs with no `busy_timeout` and no WAL.** The in-process worker thread importing a file while web requests write produces "database is locked", which the 503 handler shows as "temporarily unavailable". | `db/session.py::_connect_args` | CONFIRMED |
| R10 | Low | **`Idempotency-Key` ignores who the client acts for.** The request hash covers method, path, query and body, not `OpenClear-Act-As` / `-Act-As-Subject`: the same key and body for two people replays the first person's response and never does the second's action. | `api/idempotency.py:102-110` | CONFIRMED |
| R11 | Low | **Single-use links are redeemed check-then-set.** Embed launch codes, password-reset links, email-change links and invitations read `used_at`, then set it, with no compare-and-set. Two redemptions in the same instant both succeed (the code itself is still secret, so this needs the link first). | `services/embed_service.py::redeem`; `password_reset_service.py::complete`; `email_change_service.py::confirm`; `customer_people_service.py::accept*` | CONFIRMED |

### B. Security

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| S1 | High | **The lockout counter is a Python read-modify-write, in four copies, and sign-in has no per-route rate limit.** `user.failed_login_attempts += 1` in `authenticate_password`, `authenticate_platform`, `change_own_password` and `accept_as_existing`: parallel wrong-password requests each read N and write N+1, so the threshold is reached late; the global limit (120/min per address) is the only brake on `/ui/login` and `/api/v1/auth/login`. The two non-login copies also ignore the bank's own lockout policy (`lockout_policy`: attempts, minutes, until-unlocked) and use installation defaults. | `auth/login_service.py:148, 287`; `services/user_service.py:462`; `services/customer_people_service.py:355`; `web/routers/auth.py::login_submit`; `api/v1/auth.py::login` | CONFIRMED |
| S2 | Medium | **Refresh tokens are never rotated or revoked on use.** `refresh_session` mints a new pair with the same `sid`; the old refresh token stays valid until the session's end (up to seven days). A stolen refresh cookie or API refresh token keeps working beside the real one, and there's no reuse detection to end the session when both are used. | `services/session_service.py::refresh_session`; `web/routers/session.py`; `api/v1/auth.py::refresh` | CONFIRMED |
| S3 | Medium | **A customer's default decision at the cut-off isn't a protected change.** `FIXED_PAY` pays everything nobody decides, yet `set_customer_disposition` writes it directly under `admin:manage`, while the cut-off beside it is a `sensitive` kind under the second-person rule. The ML scoring mode has the same gap. The route also still takes `response_window_hours`, which 1.26.0 retired. | `web/routers/admin.py:229-254`; `services/change_kinds.py` (no disposition kind) | CONFIRMED |
| S4 | Low | **Permission refusals answer 404 on two web routes.** `change_decision` raises `WebNotFound` for a missing permission (a person with `exception:read` who can't decide sees "not found"). | `web/routers/exceptions.py::change_decision` | CONFIRMED |
| S5 | Low | **Search results put the search back in the address.** "More" links are `/ui/exceptions?q=...`, though 1.25.0 moved searches to POST so account numbers never reach URLs (OW-D35). | `web/routers/search.py:60` | CONFIRMED |
| S6 | Low | **Advisory and feed locks have no documented order.** (See R5.) A short rule in `docs/ARCHITECTURE.md` and one helper that takes them in order closes it for future code. | — | — |

**Checked and solid** (not repeated in the plan): every web and API route carries a permission
dependency, at module level where the listing looked bare (`_manage`, `_read`, `_reviewer`,
`_bank_wide`, `_operator`); every lookup by id goes through a tenant- or customer-scoped
repository; every `/ui` POST has `verify_csrf` or `verify_csrf_header`; tokens require `iss`,
`aud` and `exp` with per-purpose audiences and types; the WebAuthn challenge is single-use and the
sign count checked; password-reset and email-change links are hashed, single-use and expiring;
SCIM tokens compare in constant time; the markdown and plugin HTML go through nh3; the four
`|safe` uses are on sanitised or trusted content; webhook URLs are re-checked at send time;
CSV exports neutralise formula cells; the audit chain is locked and unique per bank; RLS covers
every `tenant_id` table but the ten listed in `DELIBERATELY_UNPROTECTED`, and the sweeps are
tested under the restricted role.

### C. Features: wrong, dead, or missing

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| F1 | Medium | **Four statuses are never set.** `StopPaymentStatus.EXPIRED`, `IssuedItemStatus.STALE`, `IssuedItemStatus.STOPPED` and `ExceptionStatus.ESCALATED` exist in the enums and the filters, and nothing assigns them. An expired stop stays "active" with a Cancel button and counts on the dashboard; the issued-items Status filter offers "stale" and "stopped", which never match. | `domain/stop_payment.py`, `domain/issued_item.py`, `domain/exception_item.py`; `templates/stop_payments/list.html:39`; `templates/issued_items/list.html:38-41`; `web/routers/dashboard.py` | CONFIRMED |
| F2 | Medium | **Matching ignores the issued item's own state beyond "voided".** A second presentment of an already-paid check with a different amount is only "Amount mismatch"; the stronger fact, that the check was paid, is lost unless the amounts are equal (`is_duplicate_paid`). A stop's optional amount is never compared. | `networks/check/rules.py`; `networks/check/adapter.py::_find_active_stop` | CONFIRMED |
| F3 | Medium | **Copy from before cut-offs.** The exception page's help says "A decision can't be changed afterwards" while the panel beside it says the opposite; the queue's help says "Decide by appears when a customer has a default disposition" (every item has a cut-off now); README's screenshots show the pre-1.26 decide dropdown. | `templates/exceptions/detail.html` (help_body), `templates/exceptions/list.html` (help_body); `docs/screenshots/*.png` | CONFIRMED |
| F4 | Low | **Voiding a paid check is refused only by the template.** `void_issued_item` has no status guard; the detail page hides the button, the POST doesn't refuse. | `services/issued_item_service.py::void_issued_item` | CONFIRMED |
| F5 | Medium | **The API can't do what an integrator needs around decisions.** `GET /api/v1/exceptions` has no customer filter for a bank-wide caller; there is no endpoint for the check or ACH return-reason catalogues, so an API client can't learn the `ach_return_reason_id` it must send; a decision's earlier versions (`decision_revision`) aren't exposed. | `api/v1/exceptions.py`; `api/v1/router.py` (no return-reasons router) | CONFIRMED |
| F6 | Low | **WSUD has no 60-day window.** Nacha's written statement applies to unauthorized consumer debits returned within 60 days of settlement (R10/R11); the service only checks "returned" and a consumer SEC code. The feature is also self-described as unreviewed legal text. | `services/wsud_service.py::list_wsud_eligible_transactions` | PLAUSIBLE |
| F7 | Low | **The NACHA return file's batch effective date is the original's.** A return batch normally carries the return's own effective entry date; the file passes Moov's validator, so this is a question for the bank's operator, not a defect in the record layout. | `networks/ach/returns_file.py:100` | PLAUSIBLE |

**Missing, in the order a bank's RFP would ask** (none is a defect; each is scoped in stage FR9
or deferred):

| # | Need | Today | Priority |
|---|---|---|---|
| M1 | **Paid-item (posted-item) files in the core's own layout.** Cores send posted checks as fixed-width or delimited files; OpenClear's layout builder covers issued-item files only (`import_layout_service.KIND = "issued_items"`); paid items take OpenClear's columns, X9.37 or JSON. | Absent for paid items | High |
| M2 | **Reverse positive pay** (every presented item is reviewed; the default at the cut-off is pay). | Absent; `FIXED_PAY` plus a rule that raises every item would approximate it | Medium |
| M3 | **Teller verification** before cash is paid at a branch (TELLER_PLAN.md). | Planned, not built | Medium |
| M4 | **Stop payment lifecycle**: expiry (six months is the usual default), renewal, "expires soon". | Expiry date is honoured in matching only (F1) | Medium |
| M5 | **Per-account enrollment**: which accounts have check positive pay, which have ACH filtering, which have neither. | Every account is matched; ACH has `ach_debit_block_mode` | Medium |
| M6 | **Daily digest and delegation**: one email a day instead of one per item; out-of-office cover for approvers. | Per-item, per-file and reminder emails; no digest, no delegation | Low |
| M7 | **Bank-side filters on work lists**: the exceptions queue has no customer control (only a link from the hub), the approvals queue none at all, stop payments and check images have no search or filter. | Partial | Medium (see U2) |

**Subsystems whose cost outweighs their value today** (decisions for the product owner, FR-D9):

| Subsystem | Size | Recommendation | Why |
|---|---|---|---|
| WSUD e-signature | ~400 lines, 2 tables | **Park** (hide the tile; keep the code) | A consumer remedy banks handle in the core and at the branch; incomplete (F6); its legal text is a placeholder by its own admission. |
| Billing (bank fees, statements, camt.086, platform invoices) | ~2,500 lines, 7 tables | **Freeze** | Nothing a first client asked for; account analysis is done in the core. Keep it working, build nothing more until asked. |
| Shared ML model, fraud-training examples, MRM (SR 11-7) | ~2,000 lines | **Simplify**: bank-only, advisory, off by default; drop the shared pool and the training backfill | A model trained across banks is what every bank's model-risk reviewer will refuse first; the score is advisory anyway. The anomaly-capture and replay runs stay for the bank-only model. |
| SQL Server support | dialect branches throughout | **Park** | No leader lock, no `SKIP LOCKED`, "must stay a single process"; untested in CI. |
| Compliance suite (controls, CUECs, attestations, OSCAL) | ~3,500 lines | **Keep, freeze** | It's what wins a security review; it's built; it needs no more features until a client's auditor asks. |
| Sandbox sign-up, demo tenant, plugin UI slots, SIEM CEF/LEEF, docs site with PDF | small each | **Keep** | Each is small, tested, and either sells the product or is asked for in RFPs. |

### D. Interface and journeys

| # | Sev | Finding | Where |
|---|---|---|---|
| U1 | High | **Bank staff can't work the queue by customer.** The exceptions toolbar offers Network and Status only; the customer filter exists in the route (`?customer=`) but has no control; the approvals queue has no filter, search or customer column; stop payments and check images lists have no search, filter or sort. | `templates/exceptions/list.html`, `approvals.html`, `stop_payments/list.html`, `check_images/list.html` |
| U2 | High | **The unbranded sign-in asks for a slug.** "Organization: the short name in your sign-in link" assumes the person has the link. A customer who bookmarked `/ui/login` is stuck. | `templates/auth/login.html` |
| U3 | Medium | **Stale copy** (F3), plus the decision page's "At the cut-off it becomes final and goes to the bank" when it goes within a sweep interval (R7). | `templates/exceptions/*.html` |
| U4 | Medium | **Status filters and badges show raw enum values**, including states that never occur (F1): "pending_approval", "stale", "stopped", "filter_by_authorization". | `templates/issued_items/list.html`, `customers/detail.html` (ACH debit mode badge), `check_images/*` |
| U5 | Medium | **An expired stop payment looks active** and can be "cancelled"; the dashboard counts it (F1). | `templates/stop_payments/list.html`, `dashboard.html` |
| U6 | Medium | **"Security" is five things under a heading for one.** The page is titled "Security keys"; its help covers keys only; it also holds the email address, sessions, the password and notification preferences. | `templates/security/webauthn.html` |
| U7 | Medium | **Several `<h1>` on one page.** The customer page has four (the name, "Setup checklist", "Accounts", "Self-service"); the dashboard two when the welcome card shows. Screen readers announce four page titles. | `templates/customers/detail.html`, `dashboard.html` |
| U8 | Low | **One thing, three names.** "Action Log" in the navigation, `/ui/audit-log` and `audit_log:read` in the address and permission, "audit log" in API docs; "Organization" for a bank on sign-in and "bank" everywhere else; "Unauthorized Debit Statement" as a navigation entry. | `templates/base.html`, API docs |
| U9 | Low | **Reminder and new-exception emails carry no absolute time or item details.** "The first is due in 1 hour 30 minutes" with no clock time or zone; "A new check exception needs review" with no check number or amount. | `templates/notifications/deadline_reminder_email.txt`, `exception_created_email.txt` |
| U10 | Low | **The customer page's cut-off form appears even when the bank's time zone isn't set**, with a hint to set it first; the form should be disabled until then. | `templates/customers/detail.html` |
| U11 | Low | **README screenshots are a release behind** (F3): they show "Logged in as Admin", the outcome dropdown and the old footer. | `docs/screenshots/` |

### E. Operations and tests

| # | Sev | Finding | Where |
|---|---|---|---|
| O1 | Medium | **Every web request runs about ten queries before the route**: user, session revoked, membership, group, branding, memberships elsewhere, security-key grace, session note (its own transaction), pending approvals, attention summary, customer users waiting, plus the account scope. None is cached. | `auth/deps.py::decode_and_build_context`; `web/deps.py::get_web_context` |
| O2 | Medium | **No metrics endpoint.** Job failures, outbox lag, webhook dead letters and SIEM backlog are visible on the Controls page and in logs, not to Prometheus or a pager. | `main.py` |
| O3 | Low | SQLite pragmas (R9). | `db/session.py` |
| O4 | Medium | **No concurrency tests for the money path.** `tests/test_postgres` has 36 tests; they cover the audit chain, the feed sequence and the job queue, not two decisions, a decision versus the sweep, duplicate presentment, the outbox, change requests or the lockout counter. | `tests/test_postgres/` |

## Decisions (recommended)

- **FR-D1. One lock for an item's state.** Every entry point in `decision_service` and
  `auto_disposition_service` loads the exception with `with_for_update()` (a no-op on SQLite,
  which serialises writers anyway) through a new `ExceptionRepository.get_for_update`, and
  re-reads its status after the lock. The routers map an `IntegrityError` on `decision` to
  `ALREADY_DECIDED` instead of the generic 409 page. `lock_due` selects decisions
  `with_for_update(skip_locked=True)` and re-checks `locked_at`; `_changeable_decision` locks the
  decision row. That closes R1 and R4 together.
- **FR-D2. A check's presentments are serialised on its issued item.** `CheckAdapter.evaluate`
  locks the candidate issued item (`with_for_update`) before the duplicate check, so the second
  presentment waits and then sees the first. Where there is no issued item (a "not in file"
  check), a partial unique index on `paid_item (tenant_id, account_id, check_number,
  presented_amount)` where `settlement_status = 'paid'` catches the pair; the loser becomes a
  `duplicate_paid` exception, which is what a bank expects. Migration.
- **FR-D3. The outbox takes the bank's lock and the batches are unique.**
  `decision_outbox_service.run` takes `pg_advisory_xact_lock` on a key derived from the bank id
  (the feed's scheme), and `decision_batch` gains `UniqueConstraint(tenant_id, from_sequence)`.
  Migration (one, shared with FR-D2).
- **FR-D4. Advisory locks in one order, audit last.** The rule: the feed lock is taken only
  inside `lock`; the audit-chain lock is taken after any feed lock in the same transaction. The
  act-as audit entry moves from `_acting_context` to a response hook that records it after the
  route, before commit (it names the client and the person; nothing about the route's own
  outcome). Written into `docs/ARCHITECTURE.md`.
- **FR-D5. Locks become final within a minute of the cut-off, one at a time.** `lock_due`
  commits per decision (like the auto-decide loop, isolating one row's failure) and runs from its
  own scheduled job every 60 seconds (`decision_lock_interval_seconds`), not only from the
  five-minute sweep. The pages and docs say "final at the cut-off; it reaches the bank within a
  minute". `exception.decided` keeps its meaning.
- **FR-D6. Jobs are fenced.** `_execute` passes the claim's `locked_by` to the handler's session
  (`session.info["job_lease"]`); the bulk importer's per-chunk commit is preceded by
  `UPDATE job SET heartbeat_at = now() WHERE id = :id AND locked_by = :lease`, and a rowcount of 0
  aborts the chunk. `requeue_stale` keeps its behaviour; a requeued job's old worker can no longer
  commit. The same guard goes on the data-export and dropbox handlers.
- **FR-D7. One lockout helper, atomic, by the bank's policy.** `auth/lockout.py::record_failure
  (session, user, tenant)` does `UPDATE user SET failed_login_attempts = failed_login_attempts +
  1 WHERE id = :id RETURNING failed_login_attempts`, then sets `locked_until` from
  `lockout_policy(tenant)`; `record_success` clears both. The four call sites use it; the two
  that had no tenant get it from the membership they already hold. `/ui/login` and
  `/api/v1/auth/login` gain a per-route limit keyed by address and email
  (`login_attempts_per_minute`, 20), on top of the lockout.
- **FR-D8. Refresh tokens rotate.** Each refresh token carries a `rid`; `refresh_session`
  revokes the presented `rid` (a row in `revoked_session`, keyed by rid, with the session's
  expiry) as it mints the next pair. A revoked `rid` presented again means the token was copied:
  the whole session (`sid`) is revoked and `auth.session.reuse_detected` is emitted. The web
  keep-alive already replaces both cookies on every refresh, so nothing changes for browsers;
  API clients already store the new pair.
- **FR-D9. Protected kinds for the two customer settings that move money.**
  `customer.disposition` ("A customer's default decision") and `customer.ml_mode` join
  `change_kinds` as `sensitive` kinds under `admin:manage`; `set_customer_disposition` calls
  `request_or_apply`; the `response_window_hours` field and parameter go.
- **FR-D10. Lifecycle states are rolled nightly, and the dead one goes.** Housekeeping marks
  stops `EXPIRED` once `expiration_date` has passed and issued items `STALE` once
  `stale_date_threshold_days` has passed (both reversible by a later presentment, which still
  reads the dates, so matching is unchanged). `STOPPED` is set when an active stop is created for
  an outstanding issued item and cleared when the stop is cancelled. `ESCALATED` is removed from
  the enum and the filters (a migration touches no rows: no row ever had it). Each roll touches
  `updated_at`, so `updated_since` consumers see it.
- **FR-D11. The matching rules know the issued item's state.** `IssuedItemSnapshot.status`
  `paid` raises `duplicate_paid` whatever the amount (and `amount_mismatch` too when it differs);
  a stop with an amount raises `stopped` only when the amounts match, as banks' stop orders
  work, and `stop_amount_mismatch` otherwise so the reviewer sees both facts.
- **FR-D12. The API grows the three things integrators lack.** `customer_number` filter on
  `GET /exceptions` and `GET /decisions` for bank-wide callers; `GET /return-reasons/check` and
  `/return-reasons/ach` (active entries, read with `exception:read`); `GET
  /exceptions/{id}/decision/history`. OpenAPI, SDKs and the contract test follow.
- **FR-D13. Sign-in by email first.** The unbranded `/ui/login` asks for the email alone; if its
  domain is a verified domain of exactly one organization, or the person has exactly one
  membership, the organization is chosen; otherwise the organization field appears with the
  person's organizations' names to pick from (only after the email is known, and only names the
  person belongs to, so nothing is enumerable). Branded links keep working unchanged.
- **FR-D14. Lists get the same search and filters.** The exceptions queue gets a Customer
  select for bank staff (from the hub's list), the approvals queue a Customer column and the
  shared search form, and stop payments and check images join `list_search` (status, account,
  number, date). One macro, no new patterns.
- **FR-D15. States and names in words.** A `status_label` filter for every enum shown on a
  page, used by badges and filter options; "Action log" everywhere a person reads it (the
  permission key and address stay); "Sign-in and security" for the Security page, with a section
  heading per card; one `<h1>` per page, `<h2>` for its cards.
- **FR-D16. Emails say the time.** Reminders and new-exception emails carry the deadline as a
  clock time in the bank's time zone beside the relative time, and the check or trace number and
  amount (no account numbers).
- **FR-D17. The request's context is cached where it's safe.** Branding, the group's
  permissions and `memberships_elsewhere` are cached per process for 30 seconds keyed by tenant,
  group id and user id, invalidated on the writes that change them in this process and by the
  TTL elsewhere; the three counts (`pending_approval_count`, `attention_summary`,
  `customer_users_waiting`) are computed once per request only for pages that render the
  navigation (`render_template`), not for JSON routes. Revocation checks are never cached.
- **FR-D18. Operations get numbers.** `GET /metrics` (Prometheus text, `platform:operator` key
  or a loopback-only setting): request counts and durations, queued and failed jobs by kind,
  unlocked decisions past their cut-off, unacknowledged batches, webhook deliveries by status,
  SIEM backlog. SQLite connections set `journal_mode=WAL` and `busy_timeout=5000`.
- **FR-D19. The product owner decides the portfolio** (section C's table): park WSUD, freeze
  billing, simplify ML to bank-only advisory, park SQL Server. Each is a one-line note in its
  plan and a hidden tile, not a deletion.
- **FR-D20. Product gaps get their own plans.** Paid-item layouts (M1), reverse positive pay
  (M2) and teller verification (M3, TELLER_PLAN.md) are designed after this plan ships; FR9 below
  sizes them so they can be scheduled.

## Stages

Each stage is one pull request. Before merging: the three full suites, Bandit and the dependency
audit (CLAUDE.md), and a Postgres run for the concurrency tests the stage adds.

| Stage | What | Closes |
|---|---|---|
| **FR1. The item's lock** | `get_for_update` and the re-read in every decision entry point; `IntegrityError` → "already decided"; `lock_due` with `skip_locked` and per-row commits; the 60-second lock job and its setting; the advisory-lock order and the act-as audit hook; `ARCHITECTURE.md`'s rule. Tests (Postgres, two sessions): recommend vs decide, two decides, two approvals, undo vs lock, change vs lock, a failing row in `lock_due` not holding the others. | R1, R4, R5, R7, S6 |
| **FR2. Presentments, the outbox and change requests** | The issued-item lock in `CheckAdapter.evaluate`; the partial unique index and the `decision_batch` constraint (one migration, `batch_alter_table`); the outbox lock; `with_for_update` in `change_request_service.decide/withdraw/settle`; compare-and-set for the four single-use links (`UPDATE ... WHERE used_at IS NULL`, rowcount checked); the act-as headers in the idempotency hash; job fencing. Tests: the same paid-items file imported by two workers; API retry of a presentment; scheduler and API outbox runs at once; approve twice; approve vs withdraw; a job requeued while its first worker is mid-chunk. | R2, R3, R6, R8, R10, R11 |
| **FR3. Sign-in** | `auth/lockout.py` and its four call sites; the per-route login limits; refresh rotation with reuse detection (`rid` claim, `revoked_session` reused); `auth.session.reuse_detected` in the catalogue; the ASVS rows for 7.2.4 and 6.3.2 updated with their evidence. Tests: 50 parallel wrong passwords lock at the threshold; the bank's "until unlocked" policy honoured from the password form; an old refresh token refused; reuse ends the session. | S1, S2 |
| **FR4. Protected customer settings** | The two change kinds; `set_customer_disposition` through `request_or_apply`; the field and parameter removed; the customer ML page's copy; the dual-control matrix test extended. | S3 |
| **FR5. Lifecycle states and the rules** | The nightly roll in housekeeping; `STOPPED` on stop creation and cancel; `ESCALATED` removed (migration shared with FR2 if they ship together); the void guard; the matching-rule changes and their tests (`tests/test_networks`); the dashboard's "active stops" from status; data dictionary and CHANGELOG. | F1, F2, F4, U5, M4 (the expiry half) |
| **FR6. The API** | The customer filter, the return-reason endpoints, the decision history endpoint; `scripts/export_openapi.py`, `scripts/generate_sdks.py`, the contract test; API.md and the BI guide. | F5 |
| **FR7. Copy, lists and names** | The help texts (F3, U3); the status labels (U4); the Customer select and the approvals column, search and filters on stop payments and check images (U1); the Security page's name and sections (U6); one `<h1>` per page (U7); "Action log" (U8); the emails (U9); the cut-off form disabled until the zone is set (U10); the 404s that should be 403 (S4); search links posted, not linked (S5); `scripts/generate_screenshots.py` run and the images committed (U11). The axe and viewport checks must pass unchanged; `tests/test_web/test_journeys.py` gains the customer filter and the stop-payment expiry. | F3, S4, S5, U1, U3–U11 |
| **FR8. Sign-in by email** | The email-first form and the organization pick (FR-D13); the branded links untouched; enumeration tests (an unknown email gets the same form as a known one with several organizations). | U2 |
| **FR9. Operations** | The request-context cache and the counts only for pages; `/metrics`; SQLite pragmas; the Postgres concurrency suite as a named marker (`-m concurrency`) run by `scripts/run_tests.sh postgres`; RUNBOOK sections for the new numbers. | O1–O4 |
| **FR10. Portfolio** | The owner's decisions on WSUD, billing, ML and SQL Server (FR-D19) applied: tiles hidden behind settings, plans annotated, CHANGELOG. Then the three product plans (FR-D20) written: paid-item layouts (M1, an estimated 400 lines over `bulk_import/layouts.py` and the layout builder), reverse positive pay (M2, a per-account mode and a rule), teller verification (M3, TELLER_PLAN.md). | M1–M3, M5, portfolio |

**Order.** FR1 and FR2 first, together or back to back: they are the money path, and they share
a migration. FR3 next (the only High security finding). FR4 and FR5 are small and independent.
FR6 and FR7 can run in parallel with anything. FR8 is a design change to sign-in and goes alone.
FR9 last among the fixes, since its caching touches every request and wants a quiet branch.
FR10 is decisions, then plans, not code.

**What this plan doesn't do.** No framework, no front-end build step, no new storage, no
rewrite of the decision model (one `decision` row per item stays, as UI3 decided). The feature
gaps in section C are sized, not built; each gets a plan of its own once the owner picks.

## Progress log

- 2026-10-21: Plan written from a review of `v1.26.0` (`04d915d9`) on four axes. Every finding
  checked against the code; the money-path races (R1–R4) and the lockout counter (S1) traced end
  to end. The earlier reviews' fixes spot-checked and found in place.
- 2026-10-21: FR1 built on `fr1-item-lock`: `ExceptionRepository.get_for_update` at every decision
  entry point and in the sweep (`skip_locked`); `lock_due` per decision, every minute
  (`decision_lock_job`, `decision_lock_interval_seconds`); the action-log lock before the feed
  lock, taken in `assign_sequence`, so act-as requests can't deadlock with decides. Postgres
  tests hold the item in one session and show the other waiting (recommend vs decide, two
  decides, a change vs the lock) or the cut-off skipping it (undo vs lock). SQLite suite: 2138
  passed, one failure (a test listing the scheduled jobs exactly), fixed in FR2.
- 2026-10-21: FR2 built on `fr2-presentments` (stacked on FR1; one pull request for both):
  the candidate issued item locked while a check is matched; the outbox's per-bank lock and
  `uq_decision_batch_tenant_from_sequence` (migration `07203f56ed30`, which keeps the first of
  any duplicate pair); change requests locked to decide, withdraw and settle (the sweep locks
  only those due); `db/claims.py::claim_once` for reset, email-change, invitation and embed
  links; the act-as headers in the idempotency fingerprint (only when sent, so existing keys
  still match); job leases (`jobs.check_lease` before each import chunk commits, and a
  superseded worker leaves the job's state alone).
  **Deviations, and why:** no partial unique index on `paid_item` (FR-D2's second half): a
  check that isn't in the file is never paid automatically, so two presentments of it are two
  exceptions a person sees, not a double payment, and a unique index would have made the second
  presentment fail as a row error instead of being recorded. Data-export and dropbox jobs aren't
  fenced: a second run of either repeats work but doesn't double a payment (the export writes
  another archive; the dropbox skips files by hash).

- 2026-10-21: FR3 built on `fr3-sign-in` (stacked on FR2): `auth/lockout.py` (the count incremented
  in SQL; the lock set by a conditional update, so parallel guesses lock and email once; the
  bank's policy at all four sites, and a lock that ran out locks again on the next wrong
  password); `login_attempts_per_minute` (20, per address) on web, API and platform sign-in;
  refresh tokens carry `rid` and are retired on use in `revoked_session`, with a 60-second grace
  for two tabs refreshing together and `auth.session.reuse_detected` (high) ending the session
  after it. **Deviation:** the sign-in limit is per address, not per address and email (FR-D7):
  the per-account half is the lockout itself, and keying on the email would need the body read
  before the route. ASVS: V6.3.1's evidence gains `auth/lockout.py`; refresh rotation isn't
  claimed against a row (7.2.4 is about new tokens at sign-in). Also fixed: `revoke_session`'s
  clean-up compared in-memory datetimes, which failed on SQLite once a refresh had retired a token.
- 2026-10-21: FR4 built on `fr3-sign-in` (small, so with FR3): `customer.disposition` (loosening
  only when the new default pays: fixed pay or the model's score) and `customer.ml_mode` (any
  change), both `sensitive` under `admin:manage`; the two routes go through `request_or_apply`
  and record `customer.update_disposition` / `customer.update_ml_mode` (security events
  `settings.changed`); the route's `response_window_hours` is gone. Admin docs updated.
- 2026-10-21: FR5 built on `fr3-sign-in`: `services/lifecycle_service.py` (expired stops, stale
  checks; nightly from housekeeping, per bank, then unbound again); `stop_payment_service.
  sync_issued_status` (stopped while an active stop covers the check); `STOP_AMOUNT_MISMATCH` and
  a paid candidate as a duplicate at any amount; `CantVoid` (web error, API 409); `escalated`
  removed (no migration: the column is a string and no row ever held it); OpenAPI and the Python
  and TypeScript SDKs regenerated (the .NET SDK has no status enum).
- 2026-10-21: FR6 built on `fr3-sign-in`: `api/v1/customer_filter.py` (`customer_number` on
  exceptions and the decision feed; 404 for an unknown number, 403 for a customer naming
  another); `api/v1/return_reasons.py`; `check_return_reason_id` on the API's decide, recommend
  and change (it had none, so an API client could only return a check with free text);
  `GET …/decision/history`. API.md's exceptions section rewritten (it predated 1.26.0's PUT,
  DELETE and `locked_at`). OpenAPI and all three SDKs regenerated.
- 2026-10-21: FR7 built on `fr3-sign-in`: help copy (F3, U3), `status_label` (U4), the Customer
  filter on Exceptions and Approvals, filters on Stop payments and Check images (U1), "Sign-in
  and security" (U6), one `<h1>` per page in 12 templates (U7), emails with the item and the
  bank's time (U9), the customer page's effective cut-off (U10), 403 not 404 (S4), the README
  screenshots and the desktop viewport references rewritten (U11; the accessibility baseline
  didn't grow). `exception_evidence` now gives the bank's own items their deadline.
  **Not done, and why:** S5 was wrong: the lists carry `q=` in their address by design, and
  `/ui/list-search` keeps only account numbers out of it, so posting the search's "See all"
  would change nothing. U8 (one name for the action log) is left: the navigation's title case is
  shared by every entry, and changing one would be its own inconsistency.
- 2026-10-21: FR8 built on `fr8-find-organization`. **Deviation from FR-D13:** email first, then
  choosing the organization only when there are several, tells anyone which addresses have an
  account (a known one skips a step an unknown one doesn't). Built instead: *Find your sign-in*
  (`/ui/login/find`, `services/sign_in_links.py`), which emails the address its branded sign-in
  links and answers the same for any address (ASVS 6.3.8's evidence gains it), rate-limited
  like sign-in. The plain sign-in form keeps its Organization field, with the link beside it.
  Tested under row-level security (signed out, across two banks and a customer). The end-user
  guide's sign-in section updated (it still described temporary passwords, gone since 1.22.0).
  Also on this branch: `.venv` is no longer tracked. A worktree's symlink to the virtualenv was
  committed in FR2, because `.gitignore` had `.venv/`, which matches a folder but not a link.
