# Check fraud, SARs and OFAC

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

What the Bank Secrecy Act's suspicious activity reports and OFAC's sanctions ask of a bank, where
positive pay touches them, and what OpenClear does and doesn't do. Both duties are the bank's
alone: neither OpenClear, its host nor the business customer files a SAR or screens for the bank.
This isn't legal advice ([Compliance](README.md)).

## Suspicious activity reports for check fraud

An FDIC-supervised bank files a SAR (31 CFR 1020.320; 12 CFR 353.3) for a suspected crime
involving $5,000 or more where it can identify a suspect, or $25,000 or more regardless, and for
insider abuse of any amount. It files within 30 days of first detecting the facts, or 60 when no
suspect is identified, and keeps the SAR and its supporting documentation for five years.
FinCEN's alert on mail theft-related check fraud (FIN-2023-Alert003, February 2023) asks banks to
use the key term **FIN-2023-MAILTHEFT** and to tick check fraud (field 34(d)), and to give what
they know of the accounts, places and other financial institutions involved. Its red flags include
a large check to a new payee, different check stock and a non-customer cashing large checks in
person: the cases positive pay and teller verification catch.

| What a check-fraud SAR narrative needs | Where it is in OpenClear |
|---|---|
| The account and customer | The exception; Accounts |
| The check as issued, with the payee | Issued checks; the exception's issued column |
| The check as presented, and what didn't match | The exception's presented column, with mismatches flagged; the payee read from the image |
| Images, front and back | The exception; the data export |
| What was decided, by whom, when and why | The exception's decisions; the action log |
| Attempts to cash the check at a branch | Exceptions (those refused in the last 7 days); every verification, declined or not, in the data export (`teller_verifications.json`); the action log (`teller.verify.declined`) |
| The bank of first deposit | Not kept: OpenClear doesn't read the X9.37 addenda that carry it. It's in the bank's return or image archive |
| The depositor, losses and recoveries | The bank's own systems |

**Keep SARs out of OpenClear.** A SAR, and anything that would reveal one exists, is confidential
(31 CFR 1020.320(e)). OpenClear has no field for one. Don't record a filing, or a decision not to
file, in a decision's note or reason: customer users, and support staff the bank lets in, can read
them (CUEC B-IR-6-08).

## OFAC sanctions

Every US person must not deal with a blocked person, so OpenClear's host can't either. But no rule
requires a positive pay service to screen the bank's payments, and OFAC prescribes no particular
screening; the liability falls on whoever processes the payment or holds the property, the bank.

- **Checks.** The FFIEC BSA/AML Examination Manual has a bank set risk-based procedures for
  reviewing a check's parties (issuing bank, payee, endorser). In practice the core screens
  accountholders and the teller platform screens people cashing checks.
- **ACH.** For domestic ACH the RDFI screens its own Receiver and relies on the ODFI for the
  Originator. For an inbound IAT entry the RDFI is responsible whether or not the OFAC flag is
  set. OpenClear reads IAT debits' account numbers, but not the addenda that name the parties.

OpenClear doesn't screen, and shouldn't: it sees only exceptions, not every payment, so screening
there would be incomplete, and a bank might come to rely on it. Screen where the money moves (the
core, the teller platform, the ACH processor). OpenClear shows the payee as issued, as read from
the image and as typed at a branch, so a bank can check one when it has reason to (CUEC
B-RA-3-04).

## Sources

Read 22 October 2026:
- 31 CFR 1020.320 and 12 CFR 353.3 (eCFR, current to 20 October 2026).
- FinCEN, FIN-2023-Alert003, *Nationwide Surge in Mail Theft-Related Check Fraud Schemes Targeting
  the U.S. Mail* (27 February 2023), and its Financial Trend Analysis of check fraud (September
  2024).
- FFIEC BSA/AML Examination Manual, *Office of Foreign Assets Control* (read from the Internet
  Archive's 2025 copy; the site refuses automated reading); OFAC's 1997 letter to Nacha on domestic
  ACH and OFAC FAQs 43 to 45.

**Unconfirmed:** the interagency SAR FAQs of October 2025 were read only in summaries (no duty to
document a decision not to file; no required review after filing).
