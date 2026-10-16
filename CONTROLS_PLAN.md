# Controls, control testing and CUEC attestations

COMPLIANCE_PLAN.md (C1–C5) built OpenClear's live controls: thirteen checks with `PASS`,
`WARN` or `FAIL`, a signed evidence export, model-risk tests, and crosswalks to FFIEC, Nacha,
SR 11-7 and the TPRM guidance. This plan turns those checks into a **control inventory with
testing a bank can rely on for a period**, and adds the other half: the **complementary user
entity controls** (CUECs), the things OpenClear's controls rely on the bank, its business
customers and its host to do.

**The goal:** a bank or a business customer maps each CUEC onto its own control inventory
once, and from then on its attestations come from OpenClear's test history automatically,
pulled by its GRC tool or pushed to it, with a person signing only what OpenClear can't see.

## What exists today

| Area | Today |
|---|---|
| Live checks | `services/controls_service.py`: thirteen checks (`field_encryption`, `storage_encryption`, `edge`, `audit_chain`, `segregation_of_duties`, `admin_mfa`, `anomaly_capture`, `parser_hardening`, `sla`, `api_client_secrets`, `customer_self_service`, `event_delivery`, `production_safety`). Run on each view of Settings → Controls, `GET /api/v1/controls/status`, `openclear controls`, and hourly by `controls_watch_job`. |
| Status history | `control_status` keeps **only the last status** per check, so a change becomes a `control.status.changed` security event. Nothing says how a control did over a quarter. |
| Test runs | `control_run` keeps model-risk replays, anomaly-capture runs and action-log verifications, each with its report. |
| Evidence | The signed evidence export (JSON or PDF) for a period: versions, digests, decisions, SoD breaches, changes, model runs, and the control status **at the time of export**. |
| Framework mapping | Prose crosswalks in `docs/compliance/` by theme. No control ids, and no NIST mapping. |
| User responsibilities | A short list, "What the bank is responsible for", in `docs/compliance/README.md`. Nothing for business customers or the host. Nobody can record that they did their part. |
| Customers | Business customers see no controls at all (`/controls/status` refuses a customer-scoped session). |

## What's missing

1. **Ids and a framework.** An auditor or a GRC tool needs each control to have a stable id
   and a place in a known catalog.
2. **Operating effectiveness over a period.** A snapshot says a control works now. An
   attestation (and a SOC 2 Type 2-style report) needs to say it worked all quarter, how often
   it was tested, and what went wrong and for how long.
3. **Population tests.** Some controls can be tested over every item in the period rather
   than a sample: every decision under dual control had two people; every rule change had a
   second admin. OpenClear has the data; it doesn't test it.
4. **Deviations.** A `FAIL` comes and goes with no record of why, or of what the bank did
   about it.
5. **CUECs as data**, mapped to a bank's or a customer's own controls, with attestations
   that are signed, kept, and readable by machines.

## Words used here

| Word | Meaning |
|---|---|
| **Control** | Something OpenClear does to meet a control objective (`OC-AC-05`, "Segregation of duties enforced"). From the OpenClear control inventory (CT1). |
| **Check** | A test of a control or a CUEC, run by OpenClear (`segregation_of_duties`). A control can have several checks; a check can serve controls and CUECs. |
| **Result** | One run of one check: status, evidence, its digest, and the period it covers. |
| **Deviation** | A stretch of time a check was at `FAIL` (or `WARN`, where the bank says so), with the cause and the bank's response. |
| **CUEC** | Something the bank, a customer or the host must do for OpenClear's controls to work (`B-AC-5-01`). From the CUEC inventory (CT0). |
| **Mapping** | A bank's or customer's own control (`BANK-IAM-07`) that covers a CUEC. |
| **Attestation** | A signed statement that a CUEC operated over a period, made automatically from results or by a person. |

## Decisions

| # | Question | Decision |
|---|---|---|
| CT-D1 | Which NIST inventory? | **NIST SP 800-53 Rev. 5 (release 5.1.1)**, the controls catalog, by control or enhancement id (`AC-2`, `IA-2(1)`). Exports also give NIST's OSCAL id (`ia-2.1`) so OSCAL tools match it without translation. A NIST CSF 2.0 and a SOC 2 Trust Services Criteria crosswalk come later (CT6), derived from NIST's and AICPA's published mappings, not written by hand. |
| CT-D2 | Who has CUECs? | Three parties: **bank**, **customer** (a bank's business customer) and **host** (whoever runs OpenClear: the bank when it hosts OpenClear itself, otherwise its hosting provider, for whom these are the controls the bank relies on it for). |
| CT-D3 | CUEC ids | `<party letter>-<NIST id>-<nn>`: `B-AC-5-01`, `C-IA-2-01`, `H-SC-28(1)-01`. The NIST id is part of the id, so it's readable without a lookup. Ids never change; a withdrawn CUEC is marked withdrawn and its id is never reused. When the NIST control a CUEC is filed under changes, it gets a new id and the old one is withdrawn. |
| CT-D4 | Where the inventories live | **In the code** (`src/openclear/compliance/`), versioned with the software: a release that changes them says so in the changelog, and `VERSION` moves. Banks' mappings, results and attestations live in the database. Each mapping and attestation records the inventory version it was made against; when a CUEC's text changes, its mappings are flagged for review. |
| CT-D5 | How OpenClear evidences a CUEC | Three levels, stated per CUEC: **automated** (OpenClear's own data shows it; a check can attest for a period), **assisted** (OpenClear supplies the evidence, such as an export or a recorded review; a person judges it), **attested** (outside what OpenClear can see; a person attests and may attach evidence). |
| CT-D6 | When does an automated attestation say "operated"? | Over the period: the check ran at least 95% of its scheduled times; no deviation lasted longer than the bank's grace period (default 24 hours, per bank); and every population test passed. Otherwise "operated with exceptions", listing each deviation, or "not tested" below 95% coverage. The raw numbers always go with it, so an auditor can apply their own threshold. |
| CT-D7 | Does OpenClear attest for the bank? | **No, it proposes.** At the end of each period OpenClear prepares an attestation for each automated CUEC; the bank's control owner accepts it. A mapping can be set to **accept automatically**, which is what makes attestations fully automatic; it's off by default, and turning it on is an audited action. Assisted and attested CUECs always need a person. |
| CT-D8 | Signing | Every accepted attestation is canonical JSON signed with the installation's file-signing key (ECDSA P-256, as the evidence export), carries the digests of the results it rests on, and is recorded in the action log. It can't be edited; a correction is a new attestation that supersedes it. |
| CT-D9 | Second person | Per bank: "attestations need a second person". Off by default. On, whoever prepares a manual attestation can't accept it (SoD, as C1). |
| CT-D10 | Result history | Every **scheduled** run of every check is kept (`control_test_result`); a page view reads the latest and records a result only when the status changed, so viewing doesn't flood the table. Kept for seven years by default (`OPENCLEAR_CONTROL_RESULT_RETENTION_DAYS`), pruned by housekeeping. |
| CT-D11 | Population tests | Run nightly over the previous day, and on demand for any past period. They read data OpenClear already keeps (decisions, change requests, memberships, the action log), so a bank can test a period from before it turned this on. |
| CT-D12 | Host CUECs on a shared installation | Attested **once for the installation** by the platform's staff, and shown read-only to every bank on it. A self-hosting bank attests them itself. |
| CT-D13 | Customers | Business customers see and attest only their own (`customer`) CUECs, with checks limited to their own people and accounts. The bank sees every customer's roll-up, and can ask its customers to attest each year (off by default). |
| CT-D14 | Existing API | `GET /api/v1/controls/status` and `openclear controls` stay as they are. The new endpoints sit under `/api/v1/compliance/`. |

## 1. The two inventories

### The CUEC inventory (CT0, built)

`src/openclear/compliance/cuecs.py`: 57 CUECs (30 bank, 15 customer, 12 host), filed under
43 NIST controls and enhancements (63 with related controls). Each has its NIST control and
related controls, a statement of what the party does, what OpenClear does that relies on it,
the evidence level, the check that evidences it, how often, and where the evidence is. Published by `scripts/export_cuecs.py` as
`docs/compliance/cuecs.md` (on the docs site), `cuecs.json` and `cuecs.csv` (with blank
columns for the reader's own control id and owner). `tests/test_compliance/test_cuec_inventory.py`
checks the ids, that every NIST id is a real 800-53 id with a title, that every check exists
or is planned, and that the published copies are current.

| Party | Automated | Assisted | Attested |
|---|---|---|---|
| Bank | 17 | 9 | 4 |
| Customer | 5 | 6 | 4 |
| Host | 4 | 5 | 3 |

Eleven CUECs are evidenced by a live check today; 34 name a check CT2 or CT3 adds; twelve have
no check: the eleven outside what OpenClear can see (training, risk assessment, backups,
incident handling), and sending every issued check (C-SI-7-01), which OpenClear sees only
through the exceptions a missing one causes.

### The OpenClear control inventory (CT1)

`src/openclear/compliance/controls.py`: what OpenClear itself does, in the same shape. Id
`OC-<family>-<nn>`; NIST controls; type (preventive, detective, corrective); nature
(**built in**, always on, or **configurable**, depending on a bank setting, which links it to
the CUEC that sets it); its checks; the CUECs it relies on. Proposed:

| Family | Controls |
|---|---|
| AC | Permissions checked on every request; customer scope; per-account access; segregation of duties; dual control on decisions; two-person rule and model changes; support access only by grant; tenant isolation (row-level security) |
| IA | Password policy and lockout; FIDO2 keys and passkeys; SSO (OIDC, SAML) and SCIM; API client credentials and `private_key_jwt`; customer sign-on rules |
| AU | Signed, hash-chained action log; chain verification; signed archives; security events and their delivery; evidence export |
| SC | Field encryption (AES-256-GCM, versioned); signed files; secure cookies and CSP; edge trust and origin lock-down; rate limits |
| SI | Parser hardening; input validation on uploads; anomaly capture; replay (dry run); decision deadlines, reminders and default dispositions; decision outbox with retries; signed webhooks |
| CM | Production-safety settings; migrations that upgrade and downgrade; the plugin and model digests in the evidence |
| CA | Live checks, results and deviations (this plan) |

Each existing check is attached to the control it tests. The Controls page groups checks
under their controls and shows each control's id and NIST controls.

## 2. Checks, results and deviations (CT2)

**A check registry.** The functions in `controls_service.py` move behind one registry
(`compliance/checks.py`): key, title, the controls and CUECs it serves, its schedule
(`hourly`, `nightly`, `on_view`), its kind (**state**, the system as it is now, or
**population**, every item in a period), and a scope (`bank`, `customer`, `installation`).
`controls_service.status()` keeps its output, built from the registry.

**History.** `control_test_result` keeps each scheduled run: check, status, summary,
evidence (JSON), its SHA-256, the period covered (for a population test), how it was started
(schedule, view, a person), and how long it took. `controls_watch_job` becomes the runner for
hourly checks; a nightly job runs the population checks over the previous day.

**Deviations.** `control_deviation` opens when a check goes to `FAIL` (or `WARN`, for the
checks the bank marks as such) and closes when it returns to `PASS`, linking the results at
each end. A person with `compliance:manage` records the cause and the response. The
`control_response` check reports deviations still open, and those that lasted longer than the
bank's grace period. `control.status.changed` keeps working as now.

**New checks from data OpenClear already has:**

| Check | Kind | For |
|---|---|---|
| `dual_control` | population | every decision in the period, under a bank requiring it, made by a different person from its recommender |
| `two_person_changes` | population | every rule or model change approved by a second admin |
| `change_impact_replay` | population | each approved change had a replay run against it beforehand |
| `wsud_signed` | population | each ACH debit returned as unauthorized has a signed WSUD |
| `customer_decision_timeliness` | population | each customer's exceptions decided before their deadline |
| `dormant_accounts`, `customer_dormant_users` | state | no sign-in for 90 days; deactivated later than the bank's deadline (from the action log) |
| `privileged_accounts` | state | how many hold admin permissions; dormant admins |
| `staff_mfa`, `customer_mfa` | state | second factor or SSO-only for deciders and for customers' people |
| `customer_sod` | state | customer people who can both submit issued items and decide |
| `customer_api_client_secrets` | state | a customer's API clients' secret age |
| `customer_sso`, `scim_sync` | state | the identity provider in use, and when SCIM last changed anyone |
| `support_access_review` | population | support grants in the period: each named, with a reason and an end |
| `rejected_rows` | state | upload rows rejected and not corrected within a business day |
| `stale_ach_authorizations` | state | authorizations unused for a year, or past their end and not revoked |
| `integration_tls` | state | every webhook and delivery destination is HTTPS |
| `model_pooling_acknowledged` | state | the pooling disclosure acknowledged |
| `default_dispositions` | state | every positive pay account has a default disposition |
| `evidence_export` | state | an evidence export taken within the quarter |
| `scheduled_jobs` | state (installation) | each scheduled job's last run against its schedule |
| `transport_security` | state (installation) | requests arrive over HTTPS with HSTS |
| `software_currency` | state (installation) | the running version's age |
| `platform_staff` | state (installation) | platform staff with a second factor |

## 3. Access reviews and the checks that need new data (CT3)

- **Access reviews.** A bank starts a review; OpenClear lists every membership with its
  group, scope, account access and last sign-in; a reviewer confirms or changes each one; the
  review closes when all are done and is signed into the action log. A self-managing customer
  does the same for its own people. Evidence for `access_review` and `customer_access_review`
  (B-AC-6(7)-01, C-AC-6(7)-01).
- **File control totals**: a presented-items file's item count and total as loaded, and, when
  the core sends them (NACHA batch and file control records already carry them; X9.37 has the
  bundle and cash letter controls), whether they match (`file_reconciliation`).
- **Decision delivery acknowledgement**: when the core confirms a decision file
  (`decision_delivery`).
- **Outside services**: the list of outside services turned on, each with when the bank last
  reviewed it (`outside_services`).
- **The bank's standards**: the minimum password policy and the key rotation period the
  bank records as its own (`password_policy`, `key_age`).

## 4. Mappings and attestations (CT4)

**Mapping.** For each CUEC, the bank (or customer) records one or more of its own controls:
its id (`BANK-IAM-07`), name, owner, a link to the control in its GRC tool, and whether
OpenClear's proposed attestations are accepted automatically. Mappings can be set one at a
time, or loaded from the `cuecs.csv` the bank filled in.

**Attestation.** For a CUEC and a period (monthly, quarterly or yearly, per bank):

| Field | |
|---|---|
| result | `operated`, `operated_with_exceptions`, `not_operated`, `not_applicable`, `not_tested` |
| method | `automated` (from results) or `manual` |
| statement | for a manual attestation, what the person attests |
| evidence | the results and deviations it rests on (with digests), and any files attached (`storage.py`) |
| prepared by / accepted by | a person, or "OpenClear" and the auto-accepting mapping |
| inventory version, superseded by | as CT-D4 and CT-D8 |

At the end of each period, a job prepares an attestation for each automated CUEC from its
results (CT-D6). With auto-accept, it's accepted and signed at once; otherwise the control
owner is notified and accepts it, or records why not. Assisted and attested CUECs get a
reminder before the period ends.

**Permissions** (`auth/permissions.py`):
- `compliance:read`: see controls, results, deviations, CUECs and attestations;
- `compliance:manage`: mappings, the bank's settings (period, grace, second person),
  deviation responses, starting access reviews;
- `compliance:attest`: prepare and accept attestations.

All three are kept in a customer-scoped session (not in `CUSTOMER_SCOPE_MASKED_PERMISSIONS`),
where they reach only the customer's own CUECs and checks. The bank's Admin group gets all
three; groups the bank offers to customers can include them.

**Audit and events.** `compliance.mapping.changed`, `compliance.auto_accept.changed`,
`compliance.attestation.accepted`, `compliance.attestation.rejected`,
`compliance.deviation.responded` in the action log. Turning auto-accept on, and an attestation
rejected, are security events (`services/security_events.py`). Webhooks
`compliance.attestation.prepared` and `compliance.attestation.accepted`, so a GRC tool can
take an attestation as soon as it exists.

## 5. Automating attestations from a GRC tool (CT5)

| Endpoint | What |
|---|---|
| `GET /api/v1/compliance/controls` | The OpenClear control inventory with each control's latest status |
| `GET /api/v1/compliance/controls/{id}/results?start&end` | A control's results and deviations for a period |
| `GET /api/v1/compliance/cuecs?party=` | The CUEC inventory, with this bank's (or customer's) mappings and latest attestation for each |
| `PUT /api/v1/compliance/cuecs/{id}/mappings` | Set a CUEC's mappings |
| `POST /api/v1/compliance/cuec-mappings/import` | Load mappings from the filled-in CSV (a literal path, registered before `/{id}`) |
| `GET /api/v1/compliance/cuecs/{id}/evidence?start&end` | The results and deviations behind a CUEC for a period |
| `POST /api/v1/compliance/cuecs/{id}/attestations` | Record a manual attestation, with files |
| `POST /api/v1/compliance/attestations/{id}/accept` | Accept a prepared attestation (`/reject` with a reason) |
| `GET /api/v1/compliance/attestations?start&end&format=json\|csv\|oscal` | Every attestation in a period, signed |
| `GET /api/v1/compliance/oscal/component-definition` | OpenClear's controls as an OSCAL component definition, each CUEC as a customer responsibility on the NIST control it belongs to |
| `GET /api/v1/compliance/oscal/assessment-results?start&end` | The period's results, deviations (as OSCAL findings) and attestations |

- Every list pages with `updated_since` and a cursor, as the other lists (1.13.0), so a GRC
  tool can sync incrementally.
- A read-only API client (or a customer's own API client, MOBILE_PLAN.md) is enough to pull
  attestations; accepting one needs `compliance:attest`.
- `openclear compliance cuecs|attestations|controls` in the CLI, with `--format`, and the
  SDKs regenerated.
- The evidence export gains the period's results, deviations and attestations.
- A guide, `docs/guides/grc.md`: the pull pattern (a scheduled GRC job reads attestations by
  mapping id), the push pattern (webhooks), OSCAL import, and mapping by CSV.
- The OSCAL output is validated against NIST's published OSCAL JSON schemas in the test
  suite (the schema files kept under `tests/fixtures/oscal/`).

## 6. Pages

- **Settings → Controls** gets tabs: *Controls* (by OpenClear control, its checks, a status
  timeline for the period), *Deviations*, *CUECs* (mapping, latest attestation, what's waiting
  for whom), *Access reviews*. Export evidence stays.
- **Customer self-service**: a *Your responsibilities* page with the customer's CUECs, its
  checks, mapping and attestations; access reviews of its own people.
- Lists use `responsive_table` (MOBILE_PLAN.md R1), so they work on a phone; the axe check
  covers each new page.

## Phases

One merge per phase, at most one migration each, the three full suites once per merge.

| Phase | What | Migration | Manual pass |
|---|---|---|---|
| **CT0. CUEC inventory** | `compliance/cuecs.py`; `scripts/export_cuecs.py`; `docs/compliance/cuecs.md`, `.json`, `.csv`; on the docs site; test | None | The page on the docs site |
| **CT1. Control inventory** | `compliance/controls.py`; checks attached to controls; read-only `GET /api/v1/compliance/controls` and `/cuecs` (no mappings yet); the Controls page grouped by control with ids; `docs/compliance/controls.md` generated | None | Controls page; the docs page |
| **CT2. Results, deviations, new checks** | The check registry; `control_test_result`, `control_deviation` (RLS); the runner and the nightly population job; the checks in section 2; deviation responses; retention in housekeeping | `control_test_result`, `control_deviation` | A bank with a FAIL that recovers: the timeline, the deviation, its response, the security event |
| **CT3. Access reviews and new data** | Access reviews (bank and customer); file control totals; decision delivery acknowledgement; outside-service reviews; the bank's password and key standards; their checks | `access_review`, `access_review_item`, columns on uploads and the tenant | One access review end to end, bank and customer, on a phone too |
| **CT4. Mappings and attestations** | `cuec_mapping`, `cuec_attestation` (RLS; installation rows for host CUECs); the period job; auto-accept; signing; second person; permissions; action log, security events, webhooks; the CUECs tab and *Your responsibilities* | `cuec_mapping`, `cuec_attestation` | Map from CSV; a quarter's automated attestation proposed, accepted, verified offline; a manual one with a file; a customer's |
| **CT5. Automation and exports** | The API in section 5; OSCAL component definition and assessment results; CSV/JSON; CLI; SDKs; evidence export additions; the GRC guide | None | A script that maps, pulls and verifies a quarter's attestations through a read-only client; OSCAL files through NIST's schema and one OSCAL tool (compliance-trestle) |
| **CT6. Crosswalks** | NIST CSF 2.0 and SOC 2 TSC columns, derived from the published mappings; the FFIEC page cites the NIST ids | None | — |

## Testing

- Each check: a unit test per status (`PASS`, `WARN`, `FAIL`) with `tenant_factory`; each
  population test over a period with fixed times, including a period before the feature
  existed.
- Attestation rules (CT-D6): coverage just under and over 95%; a deviation inside and outside
  the grace period; a population failure.
- The signature: an accepted attestation verifies with only the public key; one changed byte
  doesn't.
- RLS for each new table (`tests/test_postgres/test_rls_*.py`), including installation rows
  visible to every bank and writable by none.
- A customer-scoped session sees only its own CUECs and results, by page and API.
- `tests/test_openapi_contract.py` after each API change; the OSCAL schema check.
- The inventories: as CT0's test (ids, NIST ids, checks, current published copies), and that
  every control's CUECs and every CUEC's check agree.

## Progress log

(Newest last.)
- 2026-10-15 Plan written. CT0 built: the CUEC inventory (57 CUECs, NIST SP 800-53 Rev. 5),
  `scripts/export_cuecs.py`, `docs/compliance/cuecs.md`, `.json` and `.csv` on the docs site,
  and `tests/test_compliance/test_cuec_inventory.py`.
