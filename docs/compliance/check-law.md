# Check law: UCC Articles 3 and 4, Regulation CC, Check 21 and ECCHO

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

**Draft, for counsel's review.** This page makes the claims a bank relies on most, so it stays a
draft until a bank's counsel has reviewed it (a review that waits on a bank: `plans/PARKED_PLAN.md`).
It isn't legal advice ([Compliance](README.md)). The states adopt the UCC with variations; New York,
as far as we could confirm, still uses its older Articles 3 and 4, so check your state's text.

Positive pay exists because of two things: who bears a check-fraud loss, and how fast a paying
bank must return a check. This page says what OpenClear does for each rule and what the bank does.

## The deadline that matters

A paying bank must return a check before its **midnight deadline**: midnight on its next banking
day after the banking day it received the check (UCC 4-104(a)(10), 4-301). If it doesn't, it's
accountable for the check (4-302). Regulation CC adds that the return should reach the depositary
bank by 2 p.m. on the second business day after presentment (12 CFR 229.31(b)); the midnight
deadline is usually the tighter limit.

**How OpenClear keeps decisions within it** (control OC-SI-11):
- Each check's **return deadline** is worked out from the day it was presented, in the bank's
  banking days: weekdays, less the bank's closures and, unless the bank says it opens on them, the
  Federal Reserve's holidays. A check presented on a day the bank is closed counts as received on
  its next banking day.
- Its **decision deadline is never later than the bank's cut-off on that return day**, whatever
  the minimum review time or a late file would otherwise say. A check that reaches OpenClear after
  that is due at once.
- A decision **sent after the return deadline** is still sent (the bank may return late and accept
  the liability), but it's marked `after_return_deadline` in the decision feed and the
  `exception.decided` webhook, and raised as a `decision.after_return_deadline` security event
  (high). The control check *Checks decided within their return deadline* fails for 30 days after one.
- An X9.37 cash letter without a readable Business Date is refused, so a presented date is never
  guessed.

| Requirement | What OpenClear does | What the bank does |
|---|---|---|
| Midnight deadline (UCC 4-301, 4-302) | Return deadlines from the presented date; decision deadlines kept within them; late decisions flagged (OC-SI-11); decisions locked and sent at the cut-off (OC-SI-05, OC-SI-06) | Sets cut-offs that leave the core time to send returns (CUEC B-CA-3-04); records its closures and whether it opens on Federal Reserve holidays (B-SI-4-03) |
| Expeditious return (12 CFR 229.31(b)) | Sends each decision at once through the outbox | Its core sends the return |
| Notice of nonpayment, $5,000 or more (229.31(c)) | — | Its core or return process (B-CA-3-04) |
| The reason for a return (229.31(e)) | Check return reasons with ANSI X9.100-188 codes | Maps the codes to its core |
| An outage at the cut-off (229.38(e)) | Default decisions; decisions kept until the core is back | Its continuity plan (B-CP-2-01) |
| A cut-off hour (UCC 4-108) | — (ignoring it is the cautious reading) | Knows its own |

## Who bears the loss

The UCC lets a bank and its customer vary Article 4 by agreement, as long as neither disclaims
ordinary care or good faith (4-103). A positive pay agreement uses that to shift a forged or
altered check's loss to a customer who doesn't send its issue file or doesn't decide in time.

| Rule | What OpenClear records |
|---|---|
| Agreements vary Article 4 (4-103) | The settings the agreement names: cut-off, default decision, reverse positive pay (CUECs B-CA-3-02, B-CA-3-03) |
| Ordinary care (3-103(a)(9), 3-406) | Each decision: who, when, maker and checker, reason, the issued and presented details as seen, revisions, image views; the rules in force (evidence export) |
| The customer's review (4-406) | When each issued check was sent and by whom; when each exception was raised, decided or left to its default. The customer reviews its statements promptly (C-SI-4-02) |
| Properly payable (4-401) | Positive pay is how the bank catches items the customer didn't authorize |
| Impostors and fictitious payees (3-404, 3-405) | Payee matching, where the bank turns it on |
| Records for a dispute | Kept and exported, signed; the bank keeps them as long as claims can be brought (B-AU-11-02) |

## Check 21 and ECCHO

OpenClear doesn't create or transfer substitute checks, so the Check 21 warranties and indemnity
(12 CFR 229.52, 229.53) are the bank's. Since 2019, a disputed substitute or electronic check is
presumed altered rather than forged (229.38(i)); OpenClear keeps the presented image and the issued
amount and payee, which a paying bank's alteration claim needs. ECCHO Rule 9 makes a depositary bank
warrant that the drawer's signature isn't forged; the decision record and image are the evidence for
such a claim. The ECCHO Rules themselves are for members, and the bank checks them.

## Sources

Read 22 October 2026:
- 12 CFR 229, Subparts C (229.30–229.39) and D (229.51–229.60), and 229.2 (eCFR, text as of
  1 October 2026). No change to Subparts C or D since 2019. The 2017 amendments moved the paying
  bank's duties to 229.31.
- UCC 3-103, 3-404, 3-405, 3-406, 4-103, 4-104, 4-108, 4-301, 4-302, 4-401, 4-406 (the official
  text, as published by Cornell's Legal Information Institute).
- ECCHO and its Rule 9 summary (The Clearing House). The ECCHO rulebook is members-only and wasn't read.

**Unconfirmed, for counsel:** whether New York still hasn't adopted the 1990 revision of Articles 3
and 4; which states adopted the 2002 amendments; how Juneteenth fits Regulation CC's definition of a
business day (OpenClear treats it as a Federal Reserve holiday); ECCHO's return deadlines and Rule 9's
claim windows; Federal Reserve Operating Circular 3.
