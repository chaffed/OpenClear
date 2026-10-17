# Dual control for settings changes

Today a second person is needed for a handful of things: pay and return decisions (when the
bank turns on dual control), matching-rule plugins and scoring models (only while segregation
of duties is in *enforce*), support access with write rights, manual attestations (when the
bank asks) and new customer users (when the bank asks). Every other settings change, at every
level, is one person's.

This plan puts a second person on the settings changes that matter, at the three levels
OpenClear has:

- **Service provider**: whoever runs the installation for its banks (the platform's staff, the
  platform API, the installation's configuration).
- **Bank** (a tenant): the bank's own administrators.
- **Customer**: a bank's business customer, through its own administrators.

**The goal:** no single person, at any level, can weaken a protection, widen who may do what,
or send the bank's data somewhere new without a second person agreeing, and every such change
is recorded with both names.

## What exists today

| Area | Today |
|---|---|
| Two-person changes | `change_request` and `services/change_request_service.py`: two kinds (`matching_rule.set`, `ml_model.activate`), bank-level only, used only under SoD *enforce*. Settings → Changes lists them; another person with `admin:manage` approves. |
| Who can turn protections off | One admin: dual control (`tenant.update_dual_control`), SoD *enforce* → *warn* (`tenant.update_sod_mode`, which also switches off the two-person rule above), lockout, password policy, the inactivity rules, the second person on attestations, the bank's approval of new customer users. |
| Bank settings | Settings → 15 sections (`web/routers/tenant_settings.py`), plus security groups, SSO and SCIM, API clients, webhooks, plugins, the ML switch, customer self-service switches. All recorded in the action log; most raise a security event. |
| Customer settings | A self-managing customer's administrators change its people, sign-on, password rules and API clients alone (`people.py`, `sign_on.py`, `customer_password_policy.py`, `customer_api_clients.py`). |
| Service provider | Platform staff (operator view: provisioning banks, host attestations), the platform API (named keys with scopes: shared model, retraining, billing fee items, job retries), `scripts/platform_staff.py` (platform roles), environment configuration. **No second person anywhere.** The action log needs a bank, so **shared-model activation, retraining, fee items and platform role changes aren't recorded at all**; a host attestation is prepared and accepted in one step. |

## Words

| Word | Meaning |
|---|---|
| **Protected change** | A settings change that needs a second person before it takes effect. |
| **Change request** | A protected change waiting: what, the value before and after, who asked, when it expires. |
| **Approver** | Someone other than the requester who could have made the change themselves. |
| **Loosening** | A change that weakens a protection, widens who may do what, or sends data somewhere new. |
| **Tightening** | The opposite: turning a protection on, revoking, deactivating, narrowing. |

## Decisions

Recommendations, for the owner to confirm or change before DC1.

| # | Question | Recommendation |
|---|---|---|
| DC-D1 | One mechanism or one per level? | **One.** `change_request` gains a scope (provider, bank, customer), the value before and after, an expiry and a reason. Each protected change is a registered kind with a function that applies it, so a request applies exactly the code an immediate change would. |
| DC-D2 | Does tightening ever wait? | **Never.** Turning a protection on, revoking a client or a key, deactivating a person, narrowing permissions happen at once. Only loosening waits. A security response must never sit in a queue. |
| DC-D3 | Which bank changes always need a second person? | **Turning a protection off or down** (tier A below), whether or not the bank turns on anything else: dual control, SoD *enforce*, the second-admin switch itself, lockout and password rules, the inactivity rules, the second person on attestations, the bank's approval of new customer users, required SSO, security event forwarding. |
| DC-D4 | Which others? | **Tier B, behind a switch per bank** ("Sensitive settings need a second admin", off for existing banks and **on for new ones**): security groups and their permissions; SSO connections, mappings and SCIM tokens; new API clients and wider ones; webhooks and embedding origins; decision files and auto-import; plugins and the ML switch (the two existing kinds join here, no longer tied to SoD mode); customer self-service switches. Branding, messages, contact details and reminders stay one person's. |
| DC-D5 | People's access? | **A separate switch per bank**, "Access changes need a second admin" (off by default): adding someone, or giving someone a group or accounts that grant more than they had. Removing or narrowing is at once (DC-D2). Kept separate because it changes daily work most. |
| DC-D6 | A bank with only one eligible approver | **A 24-hour cooling-off instead**: the request applies itself after 24 hours unless another admin rejects it, every admin is emailed, and it's a `critical` security event ("applied without a second person"). Nothing deadlocks, and nothing changes quietly. The alternative, sending it to the service provider to approve, would let the provider change who may do what in a bank, which OpenClear has never allowed. |
| DC-D7 | Who approves? | Someone other than the requester who holds the permission the change needs (`tenant:manage`, `security_group:manage`, `user:manage`, ...), in the same scope. A customer's change: another of that customer's administrators, or the bank (`customer:manage`) when it has only one. |
| DC-D8 | What if the setting changed meanwhile? | **Approval is refused** ("it changed since this was asked"): the request records the value before, and on approval it must still be current. One open request per setting at a time. |
| DC-D9 | How long does a request wait? | **7 days**, then it expires. The requester can withdraw it; a rejection needs a reason. |
| DC-D10 | Customers | **The bank decides** ("Customers' sensitive changes need a second person", off by default), and a self-managing customer can turn it on for itself. Customer tier A (turning off its own required SSO, weakening its password rules) always needs a second person when the customer has one. |
| DC-D11 | Service provider: a log | **A provider action log**: signed and hash-chained like a bank's, in its own table (`platform_action_log`), written only from a session bound to no bank, and forwarded to the installation's SIEM destination as security events. Every provider change goes in it, with the staff member or the API key that made it. |
| DC-D12 | Service provider: which changes | **Two platform staff** for: activating or rolling back the shared model; granting or removing a platform role; provisioning, deactivating or reactivating a bank; billing fee items; creating platform API keys; accepting a host attestation (whoever prepared it can't, as CT-D9). A request made with an API key waits for a person. Retraining (which only makes a challenger) and job retries stay one step, but logged. |
| DC-D13 | Provider: emergencies | `scripts/platform_staff.py --emergency` (run on the host, so only by whoever controls it) applies at once and records a `critical` event in the provider log. It's for the installation's first operator and for lockouts, not routine use. |
| DC-D14 | The installation's configuration | Environment settings stay the provider's change management (their CUEC). OpenClear adds a check, "Installation settings unchanged", that keeps a digest of the security-relevant ones; a change opens a deviation, becomes a security event, and the evidence export carries the digest. |
| DC-D15 | Existing banks | Nothing changes for them until they act, except tier A (DC-D3), which applies from the release that ships it, announced in the changelog. Turning SoD from *enforce* to *warn* is the change most likely to be noticed. |

## 1. The mechanism (DC0)

`change_request` gains:

| Column | |
|---|---|
| `scope` | `provider`, `bank`, `customer` |
| `tenant_id` | nullable now: null for the provider (RLS as `cuec_attestation`'s installation rows: written and read only from a session bound to no bank) |
| `customer_id` | for a customer's |
| `setting` | what it changes (`sod_mode`, `security_group:<id>`), for one open request per setting |
| `before`, `after` | JSON, shown as a difference |
| `reason` | the requester's, optional; the rejecter's, required |
| `expires_at`, `applies_at` | 7 days (DC-D9); `applies_at` set for a cooling-off request (DC-D6) |
| `requested_with` | `web`, `api`, a platform API key's id |
| status | `pending`, `approved`, `rejected`, `withdrawn`, `expired`, `applied_alone` |

- **A registry of kinds** (`services/change_kinds.py`): for each, the permission needed, how to read the current value, how to tell loosening from tightening, and the function that applies it. `request_or_apply(...)` is the one call sites use: tightening, or loosening that the scope doesn't protect, applies at once; otherwise it makes a request and says so.
- **Notifications**: each eligible approver is told (a new `change_requested` notification), and the requester when it's decided. Webhooks `change_request.created` and `change_request.decided`. Security events: requested, approved, rejected, applied alone.
- **Pages**: Settings → Changes becomes the queue for every kind, each with its difference, approve, reject with a reason, withdraw. A settings form whose change was sent for approval says so ("Sent for approval: Pat or Sam can approve it"), and while one is open the form shows it and offers to withdraw it.
- **API**: `GET /api/v1/changes`, `GET /api/v1/changes/{id}`, `POST .../approve`, `.../reject`, `.../withdraw`. An API call that makes a protected change answers `202` with the request, as model activation already does.
- **The nightly job** expires old requests and applies cooling-off ones.

## 2. Bank (DC1, DC2)

| Tier | Changes | Second admin |
|---|---|---|
| A: protections | Dual control off; SoD *enforce* → *warn*; either second-admin switch off; lockout looser (more attempts, shorter, no longer until unlocked); password policy weaker; an inactivity rule off or its days longer; attestations' second person off; new customer users no longer approved; required SSO off or password sign-in back on; security event forwarding removed or sent elsewhere | Always (DC-D3) |
| B: sensitive | Security groups (new, or more permissions); SSO connections, mappings, SCIM tokens; a new API client, or one given more; webhooks (new, or a new address); embedding origins; decision files and auto-import; plugins; the ML switch; customer self-service switches | When "Sensitive settings need a second admin" is on (DC-D4) |
| Access | A new person; a person given a group or accounts with more than they had | When "Access changes need a second admin" is on (DC-D5) |
| C: everything else | Branding, contact, messages, reminders, timeouts within the limits, outside-service reviews, standards | One person |

The existing two kinds move into tier B, and SoD *enforce* no longer decides whether they need
approval: the switch does. A bank on *enforce* today gets the switch turned on by the migration,
so nothing it relies on loosens.

## 3. Customer (DC3)

- Tier A for a customer: turning off its own required SSO or putting password sign-in back on;
  a weaker password policy.
- Tier B, when on (DC-D10): its sign-on connections and mappings; its API clients; a person given
  an administrator's role.
- Approver: another of its administrators; with only one, the bank (DC-D7).
- *Your responsibilities* and the customer hub show its waiting changes; the bank sees every
  customer's under Settings → Changes.

## 4. Service provider (DC4)

- **The provider action log** (DC-D11): `platform_action_log`, chained and signed with the
  audit-log signing key, verified by the "Action log intact" check alongside each bank's, and in
  the operator view. Backfills nothing: it starts on the release that ships it.
- **Two platform staff** for DC-D12's changes, in the operator view's own Changes page. The
  platform API's shared-model activation answers `202` and waits; `scripts/platform_staff.py` and the
  other platform scripts make requests too, except `--emergency` (DC-D13).
- **Host attestations**: preparing and accepting become two people.
- **"Installation settings unchanged"** (DC-D14): a new installation check over a digest of the
  security-relevant settings (keys' fingerprints, schedulers, edge trust, SIEM destination,
  release feed, SOC 2 mapping path, sandbox inclusion), its last-seen value kept per
  installation. A change is a WARN and a deviation until a platform staff member acknowledges it
  in the operator view, which goes in the provider log.

## 5. Controls, evidence, docs (DC5)

- **Checks**:
  - `two_person_changes` widens from rules and models to every protected change: each applied
    change had a second person, or was applied alone with its cooling-off.
  - New checks: "Settings changes need a second admin" (WARN while tier B is off at a bank that
    has more than one admin), and the provider ones above.
- **Inventories**: OC-AC-07 becomes "Two people for settings changes" (rules, models,
  protections, sensitive settings, access); a new OC-AC control for the provider's; the bank's
  CUEC on segregation of duties asks it to keep a second admin. Inventory versions move
  (CT-D4).
- **Evidence export**: the period's change requests with both names and the differences; the
  provider log's verification.
- **Docs**: a section in the admin guide, the compliance pages (FFIEC "Change control", SR 11-7
  for the shared model), the SIEM guide's new events, the data dictionary, the changelog.

## Phases

| Phase | What | Tables | Shows |
|---|---|---|---|
| **DC0. Mechanism** | `change_request` widened (scope, before/after, setting, expiry, reason, statuses), RLS for provider rows; the kinds registry; `request_or_apply`; notifications, webhooks, events; Settings → Changes for every kind; the API; the nightly expiry and cooling-off | `change_request` (columns) | The two existing kinds through the new path, unchanged in behaviour |
| **DC1. Bank tier A and B** | Every tier A and B kind; the "Sensitive settings" switch (on for new banks and for banks on *enforce*); single-admin cooling-off | `tenant` (switch) | One admin can't turn off dual control or move SoD to *warn* alone; a security group change waits; a one-admin bank's change applies after 24 hours with every admin told |
| **DC2. Access** | "Access changes need a second admin": new people and wider access wait | `tenant` (switch) | Adding a person waits for a second admin; removing someone is at once |
| **DC3. Customers** | Customer tiers A and B; the bank's and the customer's switches; the bank as approver for a one-admin customer | `customer` (switch), `tenant` (switch) | A customer admin's SSO change waits for a second customer admin, or the bank |
| **DC4. Service provider** | `platform_action_log`; two platform staff for DC-D12; the API's `202`; the scripts' requests and `--emergency`; host attestations in two steps; "Installation settings unchanged" | `platform_action_log`, `installation_setting_digest` | Activating the shared model waits for a second staff member and both are in the provider log; a changed environment setting opens a deviation |
| **DC5. Controls and evidence** | The checks, inventories, evidence export, docs | None | The evidence export lists a quarter's protected changes with both names |

Each phase is merged and released on its own; DC0 first, then DC1 (the real gap), then DC2 to
DC5 in any order.

## Testing

- Each kind: tightening applies at once; loosening makes a request; the requester can't approve;
  an approver without the permission can't; approval applies exactly what an immediate change
  would; a value changed meanwhile refuses approval; one open request per setting; expiry;
  withdrawal; rejection needs a reason.
- Tier A with the switch off; tier B with it on and off; the migration turning the switch on for
  banks on *enforce*.
- Cooling-off: a one-admin bank's request applies after 24 hours, every admin is told, the event
  is `critical`; a second admin appearing meanwhile can reject it.
- Customers: a customer's change approved by its second administrator; by the bank when it has
  one; never by another customer.
- Provider: the log verifies and a changed entry breaks it; two staff for each kind; an API
  key's request waits for a person; `--emergency` applies with a `critical` event; the settings
  digest change opens a deviation.
- Row-level security: a bank sees and decides only its own requests; a customer only its own;
  provider requests and log entries readable and writable only unbound
  (`tests/test_postgres/test_rls_*.py`).
- The API's `202`s through `tests/test_openapi_contract.py`; the SDKs regenerated.
- The viewport and axe checks over the Changes pages and a settings form with a waiting change.

## Progress log

(Newest last.)
- 2026-10-17 Plan written from a review of what needs a second person today (above). Decisions
  DC-D1 to DC-D15 are recommendations, waiting for the owner.
