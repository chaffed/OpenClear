# Nacha Operating Rules

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

How OpenClear supports a bank's obligations as an RDFI under the Nacha Operating Rules, for
its ACH positive pay and unauthorized-debit handling. Check the current edition of the Rules
for exact requirements and dates. This isn't legal advice ([Compliance](README.md)).

## Protecting ACH data

The Rules require participants to protect the confidentiality and integrity of Protected
Information (non-public information, such as account numbers, used to initiate or process an
entry). Under the supplementing data security requirements, certain originators, third-party
service providers and third-party senders must also make account numbers unreadable when
stored electronically.

| Requirement | What OpenClear does | What the bank does |
|---|---|---|
| Protect Protected Information | Access by permission and segregation of duties; every access-changing action logged; tenant isolation by row-level security (Postgres); HTTPS. | Hosts OpenClear on encrypted storage, restricts network access. |
| Account numbers unreadable at rest | OpenClear matches on account numbers, so it stores them in the database. It relies on **database or disk encryption at rest**, provided by the host. | Confirms with its Rules compliance review that the host's encryption at rest meets the requirement for the bank's role. Records it in `OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION`, which the Controls page shows. |
| Secrets used to reach other systems | AES-256-GCM, rotated by key. | Rotates `OPENCLEAR_SSO_ENCRYPTION_KEY` on its schedule. |

## Unauthorized debits and returns

| Topic | What OpenClear does |
|---|---|
| ACH debit filters and blocks | Per-account authorizations by originator (and optionally receiver), with amount, frequency and SEC code limits; an account can block all debits. Debits outside them become exceptions to decide. |
| Return deadlines | Each exception has a decision deadline. Exceptions past or near it show on the Controls page; an unanswered one can be auto-decided by the customer's standing instruction. |
| Return reasons | A per-bank catalog of return reason codes; NACHA return files of decided returns. The defaults include R29 for a business account's unauthorized debit (R10 is for consumer accounts), R17 for suspected fraud, and R11's current meaning (authorized, but not as authorized). |
| Written Statement of Unauthorized Debit | The bank obtains and keeps the statement its own way (CUEC B-IR-6-02). OpenClear shows customers the bank's form, in Markdown, HTML or a PDF, and warns while a bank returning consumer debits has none published (control OC-SI-07). |

## Fraud monitoring (2026)

Since 22 June 2026 every RDFI must have risk-based processes to identify **incoming ACH credits**
initiated by fraud, and review them at least once a year; RDFIs that received 10 million or more
entries in 2023 had to from 20 March 2026. Monitoring before posting isn't required. Originators,
third-party service providers and ODFIs have a matching duty for entries they send.

**OpenClear's ACH positive pay doesn't meet this rule.** It watches debits leaving a business
customer's account; the rule is about credits arriving. What OpenClear gives the bank's program:

| The bank's program needs | What OpenClear gives |
|---|---|
| A risk assessment | Which accounts block debits, which originators each has authorized, and their exceptions and returns (data export). |
| Unauthorized-debit returns with the right reason | The per-bank return reasons with Nacha codes, including R29 and R17. |
| Evidence for the yearly review and the Rules audit | The evidence export's ACH monitoring summary (control OC-SI-10): debits by SEC code, exceptions, returns by reason code, unauthorized returns by originator, and decision times against their deadlines. |
| Credit monitoring itself | Nothing. It's the bank's own (CUEC B-SI-4-02). |

Since 1 October 2024 a consumer may sign the Written Statement of Unauthorized Debit once the entry
is **presented**, before it posts, so a customer can sign the bank's form at the exception. An
unauthorized consumer debit returned in the extended window must go back by the opening of the
sixth banking day after the bank finishes reviewing the statement (CUEC B-IR-6-04).

## Regulation E

Regulation E (12 CFR 1005) protects consumers: natural persons, with accounts opened primarily
for personal, family or household purposes. **A business account is outside it**, even when the
company debiting it, or the person paid, is a consumer. A business customer's unauthorized debits
are returned under the Nacha Rules (R29) and its agreement with the bank.

If the bank puts a consumer account on OpenClear, Regulation E runs alongside positive pay:

| Regulation E | What it asks of the bank | What OpenClear gives |
|---|---|---|
| 1005.6: liability limits; notice can be "constructively given" | Limit the consumer's liability, and treat what the bank learns as notice | The exception's history: when it was shown, who decided, when |
| 1005.11(c): investigate and decide within 10 business days (or provisionally credit and finish within 45; 20 and 90 in some cases) | Investigate, credit, report | The decision, its reason, notes and images, in the evidence and data exports |
| 1005.11(d): the documents relied on | Give them on request | The same records |

The bank marks which accounts are consumer accounts (CUEC B-RA-3-02), and treats an unauthorized
return on one as a notice of error (B-IR-6-03).

## Audit

The Rules require an annual audit of ACH Rules compliance. For the parts OpenClear handles,
the [evidence export](README.md#where-the-evidence-comes-from) gives the auditor, for the
period:
- every decision, with who recommended it and who made it;
- the authorization rules and matching code in force;
- segregation-of-duties breaches;
- changes and their approvals;
- whether the action log verifies.

The replay test (Settings → Model risk) shows how the bank's current filters would have treated
a past NACHA file, compared with what actually happened.

## Sources

Read 22 October 2026:
- 12 CFR 1005.2, 1005.6 and 1005.11, and Supplement I (eCFR, current to 20 October 2026; no
  change to these sections since 2019).
- Nacha: Risk Management Topics – Fraud Monitoring Phase 1 (effective 20 March 2026) and Phase 2
  (19 June 2026, in practice 22 June); Risk Management Topics – October 1, 2024; Differentiating
  Unauthorized Return Reasons. The Nacha Operating Rules are Nacha's publication; check the
  current edition. Whether the Phase 1 thresholds are "6 or 10 million or more" or "more than" is
  unconfirmed (Nacha's page says both); it doesn't matter since Phase 2.
