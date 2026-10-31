# Continuity plan: what OpenClear does when something is down, and how a recovery is proven

OpenClear's continuity today is attested, not measured: the host states its recovery time and point
objectives and the date of its last restore test in two settings, the Controls page shows them
(OC-CP-01), and the CUECs ask the host to back up and test restores (H-CP-9-01) and the bank to
plan and exercise an outage at the cut-off (B-CP-2-01, B-CP-2-03). The code itself resumes well
after a gap: imports hold leases, the outbox queues decisions for a core that's down, decision
files are signed so a core can take them again, and the Redis limiter degrades to each instance.
What it doesn't do is behave correctly across a gap at the cut-off, prove a backup can be
restored, or give a bank what it needs to carry on without OpenClear for a day.

Written 2026-10-31 at the product owner's request, after REVIEW_1_33_PLAN.md shipped. Nothing here
replaces the host's own platform (replication, snapshots, regions): that stays the host's, as the
CUECs assign it. OpenClear's part is to make its state survivable, its recovery verifiable, and its
behaviour after a gap right.

## What's there, and what's missing

| # | Finding | Where | Reach |
|---|---|---|---|
| BC1 | **An outage spanning a cut-off pays everything by default when the service returns.** `sweep_expired_dispositions_job` decides every open item whose deadline has passed, however long ago. Down from 10:30 to 13:00 with an 11:00 cut-off, the first sweep after restart applies FIXED_PAY to every item nobody could look at. REVIEW_1_33_PLAN.md G1's shape, caused by downtime instead of a late file. | `workers/tasks.py::sweep_expired_dispositions_job`; `services/auto_disposition_service.py` | Money: a fraudulent check on a FIXED_PAY account, paid unseen, because the service was down. |
| BC2 | **Recovery is attested, never measured.** `recovery_objectives_attestation` and `last_restore_test` are typed by the operator; nothing checks a backup exists, is fresh, or restores. | `config.py:491-496`; `controls_service._recovery_objectives` | A bank's auditor gets an assertion where evidence is possible. |
| BC3 | **The database and file storage have no consistency point.** A database snapshot and an object-store copy are taken separately; after a restore, rows may name files that aren't there (uploads, check images, exports, decision files written after the snapshot) and files may have no rows. Nothing reconciles them. | `storage.py`; the `_FILE_COLUMNS` tables | A restore that looks complete and isn't. |
| BC4 | **No read-only or degraded mode.** During a failover or a restore the only state is "503 temporarily unavailable". A customer can't see whether their decisions were taken; an administrator can't take a data export. | `main.py` (the 503 handler) | Customers acting on guesses during an incident. |
| BC5 | **A bank can't carry on by hand.** B-CP-2-01 asks the bank to get decisions to its core without OpenClear, but the only list of open items is in OpenClear. The data export is a full archive from a background job. | — | The bank's continuity plan has no artefact to work from. |
| BC6 | **`/health` checks the database only.** Storage, Redis (when configured) and the scheduler's heartbeat aren't checked, so a load balancer sees "ok" while files can't be written or the sweep hasn't run for an hour. | `main.py:524-536` | Partial failures invisible to the host's monitors. |
| BC7 | **The runbook has no recovery procedure.** It monitors and it offboards; it doesn't say in what order an installation is brought back (keys before the database; migrations; the audit-chain check; the storage reconciliation), nor what the operator tells the banks. | `RUNBOOK.md` | The restore test nobody has rehearsed. |
| BC8 | **SQLite installations have no backup story.** A single-process installation's database is a file in WAL mode; copying it live isn't a backup. | `db/session.py` | A small bank's whole record. |

## Decisions (recommended)

- **BC-D1. A gap at the cut-off gives items a recovery window, and says so.** The scheduler
  records its heartbeat (`scheduler_heartbeat`, already read by the scheduled-jobs check). On
  each sweep, if the previous heartbeat is older than the sweep interval plus five minutes, the
  gap is an outage: every open item whose `decision_deadline` fell inside it gets a new deadline
  of `min(return_deadline, now + cutoff_min_review_minutes)` (the same window RV-D7 gives a late
  file), marked `deadline_moved_reason = "outage"`, and nothing is defaulted on that sweep. One
  notice per bank to its administrators and per customer to its deciders: "OpenClear was
  unavailable from X to Y; N items due in that time are due again at Z." The gap and the count
  are a `continuity.gap` security event (high) and an entry in the bank's action log. A deadline
  moved past midnight is impossible by construction; a check whose midnight already passed stays
  due at once and is flagged as RG1 flags it.
- **BC-D2. Backups leave a manifest OpenClear can read, and the restore drill is a command.** The
  host's backup job writes `backups/<timestamp>.json` to a storage area (`backup_manifests`):
  when, what (database dump size and sha256, storage areas and object counts), where it was
  kept, retention. A `backup_freshness` check (installation scope) reads the newest and WARNs
  when it's older than the stated RPO or the manifest is missing; the Controls page shows the
  last backup beside the objectives. `scripts/restore_drill.py <manifest>` restores the dump into
  a scratch database, runs the migrations up, verifies every bank's audit chain, checks a sample
  of file hashes against the manifest, runs the reconciliation (BC-D3), and writes a signed
  result into `restore_test` rows, which become `last_restore_test` (the setting stays as an
  override for hosts that drill another way). H-CP-9-01 and H-CP-2-01 cite the drill.
- **BC-D3. A restore is reconciled.** `storage_reconcile(session)` walks the `_FILE_COLUMNS` tables
  and every storage area: rows whose file is missing, files with no row, by bank and area. Run by
  the drill, by an operator from the platform page after a restore, and nightly as a WARN-only
  check (`storage_consistency`) so drift is seen before a disaster. Rows without files are listed
  for the operator; files without rows are left (they may be a newer restore's).
- **BC-D4. A maintenance mode.** `OPENCLEAR_MAINTENANCE=1` (or a platform switch): sign-in and
  reading work; every write answers 503 with the incident banner's text ("OpenClear is being
  restored; nothing can be changed until …; your decisions already made are safe"); the data
  export is the one write allowed, so a bank can take its records. The scheduler's sweeps pause
  (and BC-D1 then treats the pause as a gap). The Controls page shows the mode.
- **BC-D5. A daily open-items snapshot for the bank's own plan.** Each bank gets, in its storage
  area and downloadable from Settings → Continuity, a signed CSV of its open and pending items as
  of the bank's end of day: customer, account, check or trace number, amount, presented date,
  decision deadline, return deadline, the customer's default. Optionally emailed to the incident
  contacts. It's what B-CP-2-01's plan works from when OpenClear is unreachable: the bank can
  decide by hand and send returns to the core before the midnight deadline.
- **BC-D6. `/ready` beside `/health`.** `/health` stays as it is (the database, for the load
  balancer). `/ready` also writes and deletes a byte in each configured storage area, pings
  Redis when configured, and reports the scheduler heartbeat's age; it answers 503 with the
  failing component named, and the metrics gain `openclear_component_up{component=…}`.
- **BC-D7. The runbook's "Recovering the installation".** Order of operations: the keys
  (JWT, file-signing, audit-log signing, the SSO encryption key) from the key store first, since
  nothing verifies without them; the database restore; `alembic upgrade head`; the audit-chain
  verification; `storage_reconcile`; maintenance mode off; an incident notice to the banks
  (RG3). What to tell banks about items decided during the gap (BC-D1's notice does most of it).
- **BC-D8. SQLite gets a nightly copy.** For a file database, housekeeping runs `VACUUM INTO` to
  the `backup_manifests` area (a consistent copy without stopping writers) and writes the manifest
  BC-D2 reads, so the freshness check and the drill work for the smallest installation too.

## Stages

Each stage is one pull request; the three suites, Bandit and the dependency audit before merging.

| Stage | What | Closes |
|---|---|---|
| **BC1. The gap** | BC-D1: the heartbeat comparison, the recovery window, the notices, `continuity.gap`, the control check `continuity_gaps` (FAIL for seven days after a gap that moved deadlines, so the event is on the Controls page). Tests: a sweep after a simulated gap moves deadlines and defaults nothing; the next sweep defaults what's still undecided; a check past midnight stays due at once; the notices. | BC1 |
| **BC2. Readiness** | BC-D6: `/ready`, the component metrics, the runbook's monitoring table. | BC6 |
| **BC3. Backups measured** | BC-D2's manifest format (documented for the host), the `backup_manifests` area, the `backup_freshness` check, BC-D8's SQLite copy. | BC2 (half), BC8 |
| **BC4. Reconciliation and the drill** | BC-D3 and `scripts/restore_drill.py` with its `restore_test` rows; `last_restore_test` read from them; H-CP-9-01, H-CP-2-01 and the FFIEC BCM page updated. | BC2, BC3 |
| **BC5. Maintenance mode and the snapshot** | BC-D4 and BC-D5; Settings → Continuity; CUEC B-CP-2-01's "assisted" evidence becomes the snapshot. | BC4, BC5 |
| **BC6. The runbook** | BC-D7, written after BC4 so it describes the real commands; a tabletop of it against the sandbox, recorded in the progress log. | BC7 |

**Order.** BC1 first: it's the money path and the only item that can pay a check wrongly. BC2 is
small and independent. BC3 and BC4 are the evidence story and go together. BC5 is features; BC6
is writing and waits for BC4.

**What this plan doesn't do.** No replication, snapshot scheduling or multi-region deployment:
the host's platform does those and the CUECs already assign them. No change to the decision
model. The host's backup job stays the host's; OpenClear only reads what it leaves behind.

## Progress log

- 2026-10-31: Plan written. Waiting for the product owner's decisions BC-D1 to BC-D8.
- 2026-10-31: BC1 built on `bc1-gap`: `services/continuity_service.py` (`recover_from_gap`, run at the
  start of the sweep, compares the sweep's own `ScheduledJobRun` with its interval plus five
  minutes; moves the deadlines that fell in the gap to `min(midnight, now + min review)`;
  `exception_item.deadline_moved_reason = "outage"`, migration `d4e8f2a6c1b7`; the action-log
  entry and `continuity.gap` (integrity, high); `notify_continuity_gap` to administrators and to
  each customer's deciders); control check `continuity_gaps` and control OC-CP-02. **Deviation
  from BC-D1:** nothing is skipped on the recovering sweep: moving the deadlines is enough, since
  the sweep decides only items whose deadline has passed, and items that were due *before* the
  gap (missed by the previous sweep for a reason of their own) are swept as usual.
- 2026-10-31: BC2 built on `bc1-gap`: `services/readiness.py` (database, a probe byte written and
  deleted in storage, Redis when configured, the sweep's last run against its interval), `GET
  /ready` (503 only for the database and storage; `degraded` for the rest), the
  `openclear_component_up` metric, the runbook's row and paragraph.
- 2026-10-31: BC3 built on `bc1-gap`: the `backup_manifests` area, `storage.read_key`,
  `services/backup_service.py` (the manifest, `freshness`, `sqlite_copy` with `VACUUM INTO` from
  housekeeping), check `backup_freshness` and control OC-CP-03, the runbook's "Backups".
- 2026-10-31: BC4 built on `bc1-gap`: `storage.exists/ref/list_keys` on every backend;
  `services/storage_reconcile.py` (`reconcile`, the `storage_consistency` nightly check, control
  OC-CP-04); `scripts/restore_drill.py` with `restore_test` rows (migration `e5f9a3b7d2c8`), read by
  the recovery-objectives check as the last restore test; Platform → Continuity (the manifest, the
  drills, "Check now"); the runbook's drill commands; H-CP-9-01 cites them.
- 2026-10-31: BC5 built on `bc1-gap`: `maintenance_mode` (the write guard in `main.py` with the
  sign-in, session and data-export paths allowed; `ctx.maintenance` and the banner; the sweep's
  pause marker, read by `gap_before`; the `maintenance_mode` check); `services/continuity_snapshot.py`
  (hourly `continuity_snapshot_job`; the signed latest file and the dated copy at the bank's 23:00;
  emailed to the incident contacts when `tenant.continuity_snapshot_emailed`, migration
  `f7a1b5c9d3e2`); Settings → Continuity with the download and the switch. **Deviation from
  BC-D4:** the mode is a setting, not a platform switch: a switch kept in the database is exactly
  what's unavailable during a restore.
- 2026-10-31: BC6 written: RUNBOOK.md "Recovering the installation", nine steps in the order each
  needs the one before. The tabletop against the sandbox is for the product owner to schedule;
  it needs a restore of the sandbox's database, which is the host's operation.
- 2026-10-31: **BC1–BC6 built** on `bc1-gap`, each stage's SQLite suite green at its commit; the
  Postgres and Linux suites on the branch head before the pull request. Three migrations
  (`d4e8f2a6c1b7`, `e5f9a3b7d2c8`, `f7a1b5c9d3e2`), four controls (OC-CP-02 to OC-CP-04 and the
  checks under them), one script (`scripts/restore_drill.py`).
- 2026-10-31 Pull request #71. CodeQL flagged the open-items download's `?day=` reaching a file name (three path-injection alerts): the route now builds the name from the parsed date, and a local `read_key` goes through `LocalStorage._path`'s resolved-path check like every other local read.
