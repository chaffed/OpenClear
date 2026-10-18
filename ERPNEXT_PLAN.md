# ERPNext integration

[ERPNext](https://github.com/frappe/erpnext) is an open-source ERP (GPL-3.0, on the MIT-licensed
Frappe framework) that many small and mid-sized businesses run their accounts payable in. To a
bank using OpenClear, an ERPNext business is a **customer**. Positive pay asks two things of
it:
- send the bank every check it writes, so presented checks can be matched (the issued-check
  file);
- decide the exceptions before the return deadline.

Today an ERPNext business does both by hand: it exports its checks to CSV and uploads them, and
it decides exceptions in OpenClear's own pages. This plan connects the two systems, so a check
written in ERPNext is in OpenClear within minutes and its exceptions are visible from ERPNext.

The [Fineract adapter](integrations/fineract/README.md) connects a bank's core system. This is
its mirror image: it connects a **customer's** system, using a customer-owned API client.

## What exists today

| Need | OpenClear | ERPNext |
|---|---|---|
| Who the integration acts as | Customer-owned API clients, limited to some of the customer's accounts if wanted; secret or `private_key_jwt` | API key and secret per ERPNext user (`Authorization: token key:secret`) |
| Checks written | `POST /api/v1/issued-items` and `/bulk` (account, check number, amount, payee, issue date); `PATCH .../{id}/void`; `Idempotency-Key` on writes | **Payment Entry**: `payment_type` Pay, `mode_of_payment`, `reference_no` (the check number), `reference_date`, `paid_amount`, `party_name`, `bank_account` (the company Bank Account); submittable (draft, submitted, cancelled) |
| The bank account | `GET /api/v1/accounts` | **Bank Account**: `bank_account_no`, `is_company_account` |
| Stop payments | `POST /api/v1/stop-payments`, `PATCH .../{id}/cancel` | None built in |
| Exceptions | `GET /api/v1/exceptions`, `POST .../{id}/decide`, `GET /api/v1/decisions` | None |
| Push from OpenClear | Webhooks (`exception.created`, `exception.deadline_approaching`, `exception.decided`), but **only a bank's admins can set them up** | Can receive webhooks only via a custom app's endpoint |
| Push from ERPNext | None needed | Webhook doctype (on submit, on cancel), signed with HMAC-SHA256 in `X-Frappe-Webhook-Signature`; or `doc_events` in a custom app |
| Clearing | Paid items (what the bank paid) | `clearance_date` on Payment Entry; Bank Reconciliation Tool |

## Mapping

| OpenClear | ERPNext Payment Entry |
|---|---|
| Account | `bank_account` → Bank Account → `bank_account_no` (matched to an account the API client can see) |
| Check number | `reference_no` |
| Amount | `paid_amount` (USD only: positive pay here is for US checks; others are skipped and reported) |
| Payee | `party_name`, the printed payee |
| Issue date | `reference_date`, falling back to `posting_date` |
| Issue | Submitted (`docstatus` 1), `payment_type` Pay, a mode of payment set up as a check (ED-4) |
| Void | Cancelled (`docstatus` 2). An amendment (cancel, then a new entry `PE-0001-1`) voids the old check and issues the new one |
| Our reference | The Payment Entry's `name`, kept by the integration with OpenClear's issued item id |

## Decisions

- **ED-1. Ship an external adapter first, then a Frappe app.**
  - **First, the adapter:** `integrations/erpnext`, like the Fineract adapter. Built on the
    Python SDK, run from cron, it reads Payment Entries over ERPNext's REST API and writes
    issued items. It works against any ERPNext, including Frappe Cloud, with only an API key on
    each side, and it needs nothing installed in the customer's ERP.
  - **Then, a Frappe app** (`openclear_erpnext`, installed in the customer's site). It adds
    what only an app can:
    - checks sent the moment a Payment Entry is submitted (`doc_events`), not on the next run;
    - an **OpenClear Exception** list inside ERPNext;
    - **OpenClear** buttons on the Payment Entry: send now, stop payment, open in OpenClear.

  Most of the value (no more manual issued-check files) comes from the adapter. The app is
  for customers who want ERPNext as their one screen.
- **ED-2. Exceptions are decided in OpenClear, not ERPNext,** in both phases. ERPNext shows
  them, their deadline and a link (or the embedded view) to decide. Two reasons:
  - **Who decided:** a decision must name the person who made it, and is subject to
    segregation of duties and dual control. Deciding through an API client would name the
    client, not the person.
  - **Deciding in ERPNext later** would need each ERPNext user mapped to an OpenClear person
    and token exchange for them. That's a later phase, ED-8.
- **ED-3. The adapter polls; OpenClear doesn't push to customers yet.** Webhooks belong to
  banks, so a customer's system can't subscribe. The adapter reads new exceptions every few
  minutes, with a cursor. That's fast enough for deadlines measured in hours.
  Customer-owned webhooks would be a separate OpenClear change, worth doing if the Frappe app
  shows the need (ED-8).
- **ED-4. Which payments are checks is configured, not guessed:** a list of Modes of Payment
  (default `Cheque`, `Check`) and the company Bank Accounts to include. A Payment Entry with
  no `reference_no` is reported, never sent with a made-up number.
- **ED-5. Idempotency and state:**
  - Each issued item is created with `Idempotency-Key: erpnext:<site>:<Payment Entry name>`,
    so a retried run can't duplicate a check.
  - The adapter keeps its cursor and the name ↔ issued item id map in
    `openclear-erpnext.sqlite`, as the Fineract adapter does.
  - The Frappe app keeps the same in a custom field on Payment Entry (`openclear_issued_item`).
- **ED-6. The API client gets the least it needs:** permissions to write issued items, voids
  and stop payments, and to read accounts and exceptions. It's scoped to the accounts in
  ED-4's list, with a `private_key_jwt` key pair (the key lives in the adapter's environment,
  or in a Password field in the app). ERPNext's side is an ERPNext user with read-only access
  to Payment Entry and Bank Account, plus write to the custom field for the app.
- **ED-7. Licences:**
  - The adapter only talks to ERPNext over HTTP, so it's **Apache-2.0** like the Fineract
    adapter.
  - The Frappe app runs inside ERPNext and imports its code, so it's **GPL-3.0-or-later**, the
    same as ERPNext.
- **ED-8. Later, and only if asked for:**
  - deciding inside ERPNext (person mapping and token exchange);
  - customer-owned webhooks in OpenClear;
  - ACH debit authorizations from a Supplier field (ERPNext has no originator id);
  - setting `clearance_date` from paid items, to feed ERPNext's bank reconciliation.

## Phases

| Phase | What lands | OpenClear changes | Outside checks |
|---|---|---|---|
| **E0. Proof** | A throwaway script against a local ERPNext (frappe_docker) that reads submitted cheque Payment Entries and prints the issued items they'd become. Settles the field mapping (ED-4, amendments, currency) on real data before anything is built. | None | ERPNext in Docker, once |
| **E1. Adapter: checks** | `integrations/erpnext`: `sync` (Bank Accounts ↔ OpenClear accounts, report the unmatched), `issued` (submitted and cancelled Payment Entries since the cursor → issued items and voids, with idempotency keys), `run` (both, exit 1 if anything is skipped). README with setup on both sides. | None expected; any gap in the API found in E0 goes in its own issue | ERPNext in Docker (`scripts/erpnext_live_check.py`, like the SSO and SIEM live checks) |
| **E2. Adapter: exceptions** | `exceptions`: new and approaching-deadline exceptions for the customer's accounts, reported (stdout, and optionally an email through ERPNext's own email queue) with a link to decide in OpenClear. | None | Same |
| **E3. Frappe app** | `openclear_erpnext`: Settings doctype (URL, client id, key, accounts, modes of payment); `doc_events` on Payment Entry submit and cancel, queued (`frappe.enqueue`), with the scheduler as a catch-up; the **OpenClear Exception** doctype refreshed every 5 minutes with deadline and link; Payment Entry buttons for send now, stop payment and open in OpenClear; custom fields shipped as fixtures. | Possibly a deep link to an exception for the embedded view | ERPNext in Docker; the app installed with `bench get-app` |
| **E4. Docs and listing** | A guide in `docs/guides/erpnext.md`, linked from the integration checklist; the app listed on the Frappe Marketplace once it's been used by a real customer. | Guide | — |

E1 and E2 can ship as one release of the adapter. E3 waits until a customer asks, as the
`MASTER_PLAN.md` waiting list does for other client-funded work.

## Testing

- **Unit tests for the mapping:** Payment Entries in and issued items out, covering:
  - submitted and cancelled entries, and an amendment;
  - a non-cheque mode of payment, a missing check number, and a foreign currency;
  - a Bank Account OpenClear doesn't know.
- **The adapter against fakes:** a faked ERPNext REST API (httpx `MockTransport`) and the SDK
  against the test app, like the Fineract adapter's tests. Re-running a step must change
  nothing (idempotency).
- **The Frappe app:** Frappe's own test runner in a bench (`bench run-tests --app openclear_erpnext`),
  in CI with the frappe_docker image.
- **Live check:** `scripts/erpnext_live_check.py` starts ERPNext in Docker, creates a supplier,
  a company Bank Account and two cheque Payment Entries (one then cancelled), and runs the
  adapter. It then asks OpenClear for one issued item and one void. It's run by hand, like the
  other live checks.

## Progress log

- 2026-10-18: Plan written. ERPNext's Payment Entry fields checked against
  `erpnext/accounts/doctype/payment_entry/payment_entry.json` (develop), and Frappe's
  webhook signing against its documentation. Not started.
