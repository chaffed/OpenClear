# Offboarding: deleting a bank's data when it leaves

GLBA's Interagency Guidelines (12 CFR 364 App. B II.B.4, III.C.4) make proper disposal of
customer information part of a bank's program, including what its service provider holds, and
201 CMR 17 and NYDFS 500.11 expect deletion at exit in the contract. OpenClear never deletes a
bank's business records: housekeeping clears only operational rows, and only the demo bank and
expired sandboxes are ever wiped. So when a bank leaves, its data stays until someone deletes it
by hand, with nothing to show the bank it's gone (`REGULATORY_PLAN.md` RG4, follow-up).

## Decisions (the product owner, 2026-10-22: "go with your recommendation")

- **OB-D1. Deletion at exit first; retention periods later.** Deleting a leaving bank's data is
  the clear gap. A bank-set retention period that deletes records as they age waits until a bank
  asks (`PARKED_PLAN.md`): banks must keep check records for years, set by state law, Regulation
  CC, the BSA and their own policy, and a wrong default destroys evidence a bank must keep.
- **OB-D2. Two steps, each a two-person change at the service provider.** *Start offboarding*
  (`platform.bank.offboard`) starts a waiting period (`bank_offboarding_wait_days`, default 30)
  and sends the bank an incident-style notice, emailed to its incident contacts and shown to its
  administrators until acknowledged, telling it to take its final data export. *Delete the bank's
  data* (`platform.bank.delete`) is refused until the wait is over. Cancelling offboarding is one
  operator: it keeps data rather than losing it.
- **OB-D3. What's deleted.** Every row of every table with a `tenant_id` (driven off the schema,
  as the demo wipe is, so a new table is covered without a code change), every file in every
  storage area, the bank's model files, and the people who belong to no other bank and have no
  platform role. The demo bank and sandboxes are refused: they have their own wipes.
- **OB-D4. What's kept.** The bank's row, inactive and stripped of its settings, so its slug is
  never reused (as an expired sandbox), and a deletion certificate. The installation's incident
  notices and the provider's own action log stay: they're the provider's records.
- **OB-D5. The action log.** It's deleted with the rest: it's the bank's record, and the bank has
  it in its final export. The certificate carries the log's entry count, last sequence number and
  last entry's hash, so the bank can check its exported copy is complete.
- **OB-D6. The certificate.** Signed like the evidence export (ECDSA P-256 over the SHA-256 of
  canonical JSON): the bank, who started offboarding and when, who asked to delete and who
  approved, when it finished, the final data export (when it was taken, or that none was), the
  action log's last entry, rows deleted per table, storage areas cleared, and people deleted or
  kept because they belong elsewhere. Downloaded from Platform → the bank, for the operator to send
  to the bank (it can no longer sign in).
- **OB-D8 to OB-D13. More safeguards** (the product owner, 2026-10-22: "allow the code that deletes a bank"):
  the operator types the bank's slug (OB-D8); deletion is refused without a full export since
  offboarding started, unless the request gives a reason, kept in the certificate (OB-D9); the
  request shows what would go, rows per table, files and people, for the approver (OB-D10); an
  approved deletion runs `bank_deletion_delay_hours` (24) later and can be stopped until then
  (OB-D11); a legal hold, put on by one operator and taken off by two, refuses it (OB-D12); every
  step is a critical security event, `bank.offboarding`, to the installation's SIEM and, while it
  exists, the bank's, and the certificate says the host's backups keep copies until they expire
  (OB-D13). A bank's own consent isn't asked: a bank leaving over a dispute may not give it.
- **OB-D7. Runs as a background job,** so a large bank doesn't hold a web request; the job has no
  bank, so deleting the bank's jobs doesn't delete it. A failed run is retried and can be resumed:
  every step is idempotent.

## Stages

| Stage | What | Status |
|---|---|---|
| OB1 | Model and migration: `tenant.offboarding_started_at`, `data_deleted_at`, `legal_hold_reason`; `bank_deletion` | Done |
| OB2 | The change kinds (offboard, delete, hold); the notice; the job and the wipe; the certificate | Done |
| OB3 | Platform pages: a bank's offboarding, the certificate's download | Done |
| OB4 | Docs: runbook, data dictionary, threat model, control OC-SI-12, CUECs, `glba.md`, CHANGELOG | Done |

## Progress log

- 2026-10-22: Plan written with the product owner's decisions OB-D1 to OB-D7.
- 2026-10-23: OB1–OB4 built with the safeguards OB-D8 to OB-D13. The row-level security test found
  that the final-export check ran with no bank bound, so it saw no export and refused every
  deletion; reads for a bank now run as that bank. The job deletes rows first and files last, and
  keeps anyone still named in another bank's records instead of failing.
