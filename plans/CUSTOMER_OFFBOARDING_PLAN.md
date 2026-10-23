# Customer offboarding: ending a customer's service and deleting its data

A bank's business customer leaves the bank, or stops positive pay and stays. Today the bank can
mark the customer inactive, which stops its sign-in, but nothing ends the rest of the service
and nothing ever deletes what OpenClear holds about it. Bank offboarding (`OFFBOARDING_PLAN.md`)
does this for a whole bank; this does it for one customer.

## Decisions (the product owner, 2026-10-23)

- **CO-D1. Deleted 90 days after the last service day.** "Positive Pay systems are generally not
  considered the system of record. The banking core and cold storage reports are usually the
  systems of record." The latest an item can come back is a consumer debit's return within 60 days
  of the statement, and once the customer has left (or dropped positive pay) a late return is
  handled by hand in any case. So the customer's data is deleted `customer_data_retention_days`
  (90) after its last service day, automatically, unless it's on legal hold. The same applies
  whether the customer leaves the bank or only stops positive pay.
- **CO-D2. Two bank administrators schedule it.** Ending a customer's service is a protected
  change (`customer.offboard`, `customer:manage`): one asks, another approves, on the bank's
  dual-control rules, with a last service day today or later. Stopping it before the last day keeps
  the service, so one administrator can; restoring a customer whose service has already ended
  reopens access, so it takes two.
- **CO-D3. The customer is told.** On approval, the customer's active people are emailed the
  last service day and asked to take their data export (Settings → Data export) before then; its
  pages show the date until then.
- **CO-D4. When the last service day passes** (the daily housekeeping run): the customer is
  inactive, so its people and API clients can't sign in and tellers are told its checks aren't on
  positive pay (cashed by the bank's procedure, `TELLER_PLAN.md`); their sessions end; files that
  name its accounts are refused row by row ("positive pay ended for this account's customer"), so
  the core's file shows it still sends them; open exceptions take the account's default at their
  deadline, as any undecided item does.
- **CO-D5. What's deleted.** The customer and every row that refers to it, directly or through its
  accounts, items, exceptions and uploads (followed through the schema's foreign keys, so a table
  added later is covered), their files (check images, uploads, export archives), and the people
  who belong to no other customer or bank. **Kept:** the bank's action log and security events
  (the bank's own records, chained and signed, so they can't lose entries), and a deletion record
  with a signed certificate the bank's administrators can download.
- **CO-D6. Safeguards,** as for a bank where they fit: a legal hold (one administrator puts it on,
  with the reason; two take it off) refuses deletion; the customer page shows the deletion date
  from the moment it's scheduled; each step is in the action log and a security event
  (`customer.offboarding`, high); every check is made again when the deletion runs.

## Stages

| Stage | What | Status |
|---|---|---|
| CO1 | Model and migration: `customer.service_ends_at`, `service_ended_at`, `legal_hold_reason`; `customer_deletion` (RLS) | Done |
| CO2 | The change kinds; the email; ending service and deleting in the daily run; the certificate; imports refusing ended accounts | Done |
| CO3 | The customer page's Offboarding section; Deleted customers and the certificate's download | Done |
| CO4 | Docs, data dictionary, threat model, control OC-SI-13, CUEC B-SI-12-02, CHANGELOG | Done |

## Progress log

- 2026-10-23: Plan written with the product owner's decisions CO-D1 to CO-D6.
- 2026-10-23: CO1–CO4 built. A person is deleted with their own sign-in material, messages and
  identity links; anyone named in the bank's records (the action log, decisions, approvals) is
  kept and deactivated, so those records still say who acted. This applies to bank offboarding too.
