# OpenClear compliance, control and model risk testing plan

This plan implements [`docs/specs/openclear_compliance_control_testing_suite_specification.md`](../docs/specs/openclear_compliance_control_testing_suite_specification.md) (the spec). The spec describes a Go layout (`pkg/controls/rbac.go`, `cmd/openclear-cli`); OpenClear is Python. This plan maps each component onto what OpenClear already has, and notes where it deliberately differs from the spec.

## What already exists

| Spec asks for | OpenClear has |
|---|---|
| RBAC middleware for API and UI | Per-bank security groups with fine-grained permissions, checked on every API and web request (`auth/permissions.py`, `auth/deps.py`, `web/deps.py`) |
| Dual control | Maker-checker on pay/return decisions, per bank (`services/decision_service.py`) |
| An immutable, signed audit log (HMAC SHA-256, ISO 8601) | A hash-chained audit log, each entry **ECDSA-signed** (stronger than an HMAC, since the verifier needs no secret), with checkpoints and archives (`services/audit_log_service.py`, `audit_archive_service.py`). Times are timezone-aware. **We keep ECDSA rather than downgrading to an HMAC.** |
| File digests | Every uploaded file is stored with its SHA-256 and a signature (`bulk_upload_file_service`) |
| A CLI | `openclear` (Python SDK): `push`, `decisions`, `webhooks` |
| A synthetic generator | `scripts/sandbox_test_files.py` (Moov-built X9.37 and NACHA files from CheckSim, with expected outcomes) |

## Phases

| Phase | Spec | What | Notes |
|---|---|---|---|
| C1 | A | **Segregation of duties.** Conflicting-permission rules per bank, with defaults: whoever submits items can't decide them, and whoever manages users can't define what permissions grant. **Enforced on security groups** (a group can't hold both sides) **and per transaction** (the person who submitted an item can't decide its exception). A breach attempt gets `403` with `ERR_SOD_VIOLATION`, and a `sod.violation` entry goes into the audit log at once. **Changes to rules and models need a second person:** turning on a matching-rule plugin and promoting an ML model wait for another admin's approval (the spec's `RuleAuthor` can't `RuleDeploy`). | Per bank: `warn` (every bank starts here) or `enforce`. Every warning is still logged. |
| C2 | B | **Model risk management.** A replay engine (`POST /api/v1/mrm/replay`) runs a historical X9.37 or NACHA file through today's rules in **dry run**: everything happens inside a transaction that is rolled back. It reports how today's rules compare with what actually happened to the same items (match rate, false positives, missed returns, unhandled items). A synthetic anomaly generator injects known fraud vectors (duplicate serials, altered ACH amounts, mismatched accounts, malformed fields). A **daily anomaly-capture test** runs it against the bank's live rules in dry run and records whether every injected anomaly was caught. | **ISO 20022 RTP:** OpenClear has no RTP network yet, so replay covers check and ACH. RTP replay comes with an RTP network. |
| C3 | C | **Evidence export.** `GET /api/v1/audit/export?start&end&format=json\|pdf` and `openclear audit export --start --end --format json\|pdf`. The bundle covers: the system version; plugin and rule versions and their digests; the audit chain's verification; SoD violations; decision histories with who decided; a live control status snapshot. **It's signed** with the installation's file-signing key, so an examiner can check it hasn't been altered. | |
| C4 | D | **Continuous control monitoring.** `GET /api/v1/controls/status` and Admin → Controls, with `PASS`, `WARN` or `FAIL` and evidence for each control. The controls: data at rest; SLA timeliness (exceptions past or near their deadline, stuck imports, the outbox running late); **parser hardening** (a corpus of malformed X9.37, NACHA and CSV files run through the real parsers, which must reject each cleanly and quickly); audit chain integrity; SoD conflicts; MFA coverage of admins; production-safety settings; API client secret age; the last anomaly-capture result. **Data at rest gets an upgrade:** secrets in the database move from Fernet (AES-128) to **AES-256-GCM** with key versioning, re-encrypted in place. | Disk and database encryption are the host's job. The control reports them as "verify with your provider" (WARN) rather than claiming what it can't see. |
| C5 | Step 4 | **Compliance artifacts** (`docs/compliance/`): crosswalks to FFIEC IT booklets, the NACHA rules on data security, SR 11-7 model validation, and the 2023 interagency TPRM guidance, plus a TPRM due-diligence pack. | These map features to requirements. They aren't legal advice, and each bank's own assessment still decides. |

## Decisions

| # | Question | Decision |
|---|---|---|
| C-D1 | Language and layout | Python, in OpenClear's existing structure; the spec's Go paths are illustrative |
| C-D2 | Audit signatures | Keep ECDSA, already stronger than the spec's HMAC |
| C-D3 | SoD strictness | Every bank starts at `warn`, new or upgraded: the default Admin and Bookkeeper groups combine duties, so enforcing from day one would refuse the bank's own admin. `enforce` is refused while any group still conflicts, and the Controls page (C4) flags banks still on `warn` |
| C-D4 | Dry run | A rolled-back transaction around the real ingestion code, not a separate rules copy, so replay can't drift from production |
| C-D5 | Field encryption | AES-256-GCM, versioned; old Fernet values are still read and are re-encrypted |

## Progress log

(Newest last.)
- 2026-10-09 Plan written from the spec.
- 2026-10-09 C1 done: SoD rules and modes (`auth/sod.py`), the per-transaction presenter check, group checks, two-person changes for matching rules and ML models (`change_request_service`, Admin → Changes), Settings → Segregation of duties. Tests: `tests/test_auth/test_sod.py`.
- 2026-10-09 C1 follow-up: the API's model activation also waits for a second admin under enforce (`202`, pending). Train-time promotion (champion/challenger) and the platform's shared model stay automatic: the first is a rule, not a person's change, and the second is outside any bank's duties.
- 2026-10-09 C2 done: `services/mrm_service.py` (replay with a variance report; anomaly capture with check, ACH and malformed-file probes), `control_run` table, `/api/v1/mrm`, Admin → Model risk, the opt-in daily job (`enable_control_test_scheduler`). The dry run is a savepoint rolled back around the real ingestion code. A replay "rewinds" each original item inside it, setting the paid item back to pending and moving its ACH trace number aside, so an item isn't flagged as a duplicate of itself. Check images aren't replayed (they're stored outside the database), so the OCR payee check doesn't run, and the report says so.
- 2026-10-09 C4 done before C3, since the evidence bundle includes the control snapshot. `services/controls_service.py` (ten controls), `/api/v1/controls/status`, Admin → Controls, `openclear controls`. AES-256-GCM field encryption with key ids, rotation (`sso_encryption_key_previous`) and re-encryption at startup (`auth/crypto.py`). Host encryption is reported from `storage_encryption_attestation`.
- 2026-10-09 C3 done: `services/evidence_service.py`, `GET /api/v1/audit/export` (JSON or PDF, signed), Admin → Controls → Export evidence, `openclear audit export`.
- 2026-10-09 C5 done: `docs/compliance/` (README, ffiec, nacha, sr-11-7, tprm with a due-diligence pack), added to the docs site.
