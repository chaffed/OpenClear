# Nacha Operating Rules

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
| Return reasons | A per-bank catalog of return reason codes; NACHA return files of decided returns. |
| Written Statement of Unauthorized Debit | Customers sign WSUDs in OpenClear. Each is signed (ECDSA) with the consent and attestation text the bank's counsel supplies, and kept with the debits it covers. |

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
