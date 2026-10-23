# Parked: what waits on a bank, a customer or infrastructure

Everything the other plans left for later because it can't be built, or can't be checked,
without something outside the project: a bank's systems, a customer who asks, real
infrastructure, or money. Each item says what it waits for and where it came from, so it can
be picked up when that arrives. Items that only need building are in their own plans, not here.

Collected on 2026-10-22, at the product owner's request, from a survey of every plan.

## Waiting on a bank

| Item | Waits for | From |
|---|---|---|
| **Teller reference integration**: a worked example and test harness for the first bank's teller platform, and the response-time target (300 ms at the 95th percentile) in the controls | A bank's teller platform and its test environment | TELLER_PLAN.md TL-3 |
| **Teller exceptions**: a mismatch at the counter waits minutes for the customer to approve from a phone, instead of a decline | A bank asking for it | TELLER_PLAN.md TL-4 |
| **Check returns as an X9.37 return file** | A bank that needs returns in that form | SDK_PLAN.md |
| **X9.37 fields checked against a real bank's implementation** | A bank implementing X9.37 with OpenClear | SCALE_PLAN.md |
| **Reference plugins for Banno, Alkami and Q2** (online banking) | A bank on one of them asking | SDK_PLAN.md X8 |
| **Edge mTLS for API clients** (client certificates pinned at the edge) | A bank asking; designed but hidden | WAF_PLAN.md W6; MOBILE_PLAN.md MB-D2 |
| **Pushing edge rules through the provider's API** | A bank whose edge provider and policy call for it | WAF_PLAN.md W-D6 |
| **ASVS 10.3.5 and 10.4.14 at Level 3** | Data no bank's core sends today | OWASP_PLAN.md |
| **Per-bank enablement for payment networks** | A network plugin existing | PLUGIN_PLAN.md PL2 |
| **A direct CRI Profile crosswalk** (and confirming its licence lets it be published); until then banks use the CSF 2.0 crosswalk | A bank that assesses with the CRI Profile | REGULATORY_PLAN.md RG-D6 |
| **Counsel's review of the check-law page** (UCC 3/4, Regulation CC, Check 21 and ECCHO); the page is published as a draft until then | A bank's counsel | REGULATORY_PLAN.md RG-D4 |

## Waiting on a customer

| Item | Waits for | From |
|---|---|---|
| **ERPNext app** (the Frappe app built on the adapters) | A customer on ERPNext asking | ERPNEXT_PLAN.md E3 |
| **ERPNext Marketplace listing** | A real customer to list with | ERPNEXT_PLAN.md E4 |
| **Deciding inside ERPNext**, customer webhooks, ACH authorizations and `clearance_date` | Asked for | ERPNEXT_PLAN.md ED-8 |
| **Customer-defined security groups**, within a limit the bank sets | A customer asking | CUSTOMER_ADMIN_PLAN.md CA-D2 |
| **Billing extras**: finalized platform invoices, a NACHA debit file for hard charges, earnings-credit calculation | Billing unfrozen, and a bank asking (billing is frozen: FULL_REVIEW_PLAN.md FR-D19) | BILLING_PLAN.md B-D1, B-D7 |

## Waiting on infrastructure, or a first client to fund it

The product owner's decision of 2026-10-12: these wait for a first client, who would also fund them.

| Item | Waits for | From |
|---|---|---|
| **User-testing sessions** with AP clerks and bank staff, and the follow-ups they find | People to test with | MASTER_PLAN.md; UX_PLAN.md |
| **A manual screen-reader and keyboard pass**, by a person | Someone to do it | UX_PLAN.md U10 |
| **Confirming the edge on a real zone** (Cloudflare, then Akamai); built and tested against a stub only | A real zone | MASTER_PLAN.md M7; WAF_PLAN.md W0 |
| **Publishing the SDKs** to PyPI and npm, and the docs site | Approval to publish | MASTER_PLAN.md; SDK_PLAN.md |
| **An independent penetration test**; its report joins the third-party risk pack | Funding | OWASP_PLAN.md OW-D2 |

## Dates

- **The OWASP SAMM re-score is due by 2027-10-17** (`docs/security/samm.md`). It doesn't wait
  on anything; it's here so the date isn't lost.

## Progress log

- 2026-10-22: Plan written from a survey of every plan. Nothing is built here: an item moves out
  when what it waits for arrives, into its own plan or a new one.
- 2026-10-22: Counsel's review of the check-law page added (REGULATORY_PLAN.md RG-D4).
- 2026-10-22: A direct CRI Profile crosswalk added (REGULATORY_PLAN.md RG-D6).

