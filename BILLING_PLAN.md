# OpenClear billing plan

Started 2026-10-08. OpenClear prices its positive pay services, both for a bank's customers
and for the platform operator's banks, and produces the standard billing outputs (an API,
reports, an account-analysis feed and hard-charge postings). Tracked like FIX_PLAN.md and
SCALE_PLAN.md: each phase gets a branch, a progress log, and full test runs before merging.

## What the standards say (research, 2026-10-08)

- **AFP Service Codes** are the industry's standard for classifying bank service fees.
  Banks assign one to each treasury service, and it's a mandatory field in the
  electronic analysis statement (ANSI X12 822). Domestic codes are 6 characters
  (e.g. `010000` DDA maintenance); the newer AFP Global codes are 8. **Banks don't agree on
  codes for positive pay**: published fee schedules show `15 0120` and `15 0310`
  (positive pay items and exceptions), `20 0000` and `20 0399` (positive pay maintenance
  and exceptions), and `25 0202` for ACH received with ACH positive pay. So OpenClear
  stores each bank's own AFP code (and its internal bank service code) on every fee, with
  no fixed codes built in. The AFP code book is licensed by AFP; banks have their own copy.
- **ISO 20022 camt.086 (Bank Services Billing, "BSB")** is the XML equivalent of the X12
  822 and the TWIST BSB. Its specification is published openly. It's the electronic
  billing statement OpenClear exports. Structure: `BkSvcsBllgStmt` (root) with `RptHdr`,
  then `BllgStmtGrp` (sender and receiver), then `BllgStmt` per account. Each statement has
  `StmtId`, `FrToDt`, `CreDtTm`, `Sts`, `AcctChrtcs` (`AcctLvl`, `CshAcct`, `CompstnMtd`,
  `DbtAcct`), and one `Svc` per service: `SvcDtl/BkSvc` (`Id`, `Desc`, `CmonCd` = the AFP
  code), `Vol`, `Pric/UnitPric`, `Pric/Mtd`, `PmtMtd`, `OrgnlChrgPric` and `TaxDsgnt`.
  Code lists from the ISO message definition report (2020–21 maintenance, camt.086.001.04):
  - **Service payment method** (`PmtMtd`): **`BCMP` BalanceCompensable** ("allows the bank
    to offset a service charge with interest earned on the deposit account balance…
    used in the USA") = **account analysis**. **`FLAT` HardCharge** ("charge is not
    balance compensable and is due") = **hard charge**. Also `WVED` Waived, `FREE`,
    `PVCH` PreviouslyCharged, `INVS` InvoicedSeparately.
  - **Compensation method** (`CompstnMtd`, per account): `DBTD` Debited, `INVD`
    Invoiced, `DDBT` DelayedDebit, `NOCP` (doesn't pay).
  - **Charge method** (`Mtd`): `UPRC` unit priced (default), `FCHG` flat, `MCHG` minimum,
    `TIR1`–`TIR9` volume tiers, `DPRC` discounted price, `BCHG` base charge.
  - **Statement status** (`Sts`): `ORGN` original, `RPLC` replaces, `TEST`.
  - **Tax designation**: `XMPT` exempt, `ZERO`, `TAXE`.
- **Account analysis vs. hard charge:** on an analyzed account, monthly service charges are
  offset by an *earnings credit* (imputed interest on collected balances at the bank's
  earnings credit rate, ECR); only any shortfall is charged. A hard charge is billed
  directly, typically debited from a designated account. **OpenClear doesn't calculate
  earnings credits.** Balances and the ECR live in the core and its analysis system;
  OpenClear supplies the priced service lines (`BCMP`) for the analysis system to apply.

## Design

**Who bills whom:**
1. **Bank to customer:** each customer is flagged **analysis**, **hard charge** or
   **exempt**, with a billing account (the account analyzed, or debited).
2. **Bank-wide:** accounts not assigned to a customer, and bank-level services like SMS
   notifications, are billed by a bank-level setting, using the same three methods.
3. **Platform operator to bank:** the operator (a CUSO, hosting partner, or anyone
   running OpenClear for several banks) prices each bank's usage with its own fee
   schedule. It's always a hard charge (an invoice); a platform API key with a new
   `billing` scope gets to it. (`/api/v1/platform/usage` stays as the raw counts.)

**Fee schedule:** a list of fee items per owner (a bank, or the platform), each with:
- a code, description and metric (below);
- the AFP code and bank service code;
- a unit price (4 decimal places, e.g. $0.0350 per item);
- optional volume tiers (`TIRn`), a monthly minimum (`MCHG`), active dates, and
  taxable (optional).

**Per-customer terms:**
- billing method and billing account;
- a percentage discount;
- per-fee overrides (a negotiated price, or waived);
- waive everything (e.g. a promotional period).

**Metrics: what can be charged.**
- Monthly charges (counted as of the end of the period):
  - per bank (platform billing);
  - per customer;
  - per account;
  - per account with ACH positive pay;
  - per user;
  - per active ACH authorization rule;
  - per customer with its own fraud model.
- Per-item charges (counted within the period):
  - issued checks loaded, and issue files uploaded;
  - checks presented (evaluated), and check exceptions;
  - ACH debits evaluated, and ACH exceptions;
  - items paid on decision, and check and ACH items returned;
  - items decided automatically (default disposition);
  - stop payments placed;
  - check images processed (OCR / payee positive pay);
  - unauthorized-debit statements (WSUD) signed;
  - bulk files received (any kind);
  - data exports;
  - SMS and email notifications sent (bank-wide only: a notification isn't tied to a
    customer).

**Statements:**
- Monthly by default; any date range can be previewed.
- One statement per customer (plus one bank-wide), with lines per fee item: volume, unit
  price, charge method, list charge, discount, net charge, and payment method (`BCMP`,
  `FLAT`, `WVED` or `FREE`).
- Account-level volumes are kept per line, for the analysis feed.
- **Preview** is calculated live and never saved. **Finalize** saves the statements for
  a period (status `ORGN`), locked and audit-logged. Finalizing again replaces them (`RPLC`),
  and the earlier version is kept.

**Outputs** (each as both API and web downloads):
1. **Billing report:** a period summary by fee and by customer, and a statement per
   customer (HTML on screen, CSV, PDF).
2. **Account analysis feed** (analysis customers): CSV with one row per billing account
   and fee (period, account, AFP code, bank service code, description, volume, unit price,
   charge, `BCMP`), and **camt.086 XML**.
3. **Hard-charge postings** (hard-charge customers): CSV of one debit per customer
   (billing account, amount, description, period) for the core's fee-posting import,
   plus the per-customer statement as the invoice.
4. **JSON API** for all of the above.

**Permissions:**
- `billing:read`: view statements and reports. A customer-scoped user sees only their own
  customer's statement.
- `billing:manage`: the fee schedule, customer billing terms, and finalizing. Masked for
  customer-scoped users.

## Decisions (recommended; adopted per the standing instruction, change any of them)

| # | Question | Decision |
|---|---|---|
| B-D1 | Calculate earnings credits in OpenClear? | **No.** Export `BCMP` lines; the core/analysis system applies the ECR |
| B-D2 | AFP codes | **Each bank enters its own** on each fee; starter fees ship with none |
| B-D3 | Where tiers and minimums apply | **Per customer, across all its accounts** (relationship pricing); charges land on the billing account |
| B-D4 | Preview vs. finalize | **Both:** a live preview, and a locked, finalized snapshot that can be replaced |
| B-D5 | Platform-to-bank billing | **Included** (phase B4), always hard charge, behind a `billing` platform-key scope |
| B-D6 | Census definition for "monthly" metrics | **Active at the end of the period** (created on or before it, currently active), the same approach as usage metering |
| B-D7 | Hard-charge collection | **A CSV posting file for the core** (`DBTD`); no NACHA debit file in v1 |
| B-D8 | camt.086 version | Follows the ISO message definition structure. The namespace version is a setting; **validate against your counterparty's XSD before production use** |

## Phases

| Phase | What | Status |
|---|---|---|
| B1 | Data model, metric catalog, pricing engine, customer and bank billing terms, live preview, API and CSV report | done |
| B2 | Finalize/replace snapshots, analysis feed (CSV + camt.086), hard-charge postings, PDF statement, audit log | in progress |
| B3 | Web UI: fee schedule, customer billing terms, billing reports, customer self-view | |
| B4 | Platform-to-bank billing (`billing` scope), docs (README, API.md, admin docs) | |

## Progress log

(Newest last.)
- 2026-10-08 Research done; plan written; B1 started on branch `billing`.

## Sources

- AFP Service Codes: https://www.financialprofessionals.org/training-resources/resources/afp-service-codes ·
  https://www.financialprofessionals.org/docs/default-source/default-document-library/pub/decoding-the-new-afp-service-codes.pdf
- Published fee schedules showing positive pay AFP codes: https://deerparktx.gov/DocumentCenter/View/6662 ·
  https://treasurer.state.md.us/wp-content/uploads/2024/02/td-bank-fees.pdf ·
  https://www.huntington.com/-/media/pdf/commercial/tm/receivables-management/hnb-service-code-descriptions-fis-lockbox.pdf
- camt.086, TWIST BSB and X12 822: https://www.theglobaltreasurer.com/2013/02/21/the-822-the-twist-bsb-and-the-iso-bsb-whats-it-all-about/ ·
  https://www.financialprofessionals.org/docs/default-source/default-document-library/sp/81.pdf
- ISO 20022 Bank Services Billing message definition report (camt.086.001.04, 2020–21
  maintenance): https://www.iso20022.org/sites/default/files/2020-12/ISO20022_MDRPart2_BankServicesBilling_2020_2021_v1_ForSEGReview.pdf
- Account analysis and earnings credits: https://www.financialprofessionals.org/training-resources/resources/articles/Details/an-introduction-to-account-analysis-statements ·
  https://southstatecorrespondent.com/banker-to-banker/treasury-cash-management/how-to-use-account-analysis-effectively-part-1/
- 2026-10-08 B1 done:
  - **Model:** `billing_fee_item`, `customer_billing_terms` and `customer_fee_override`, plus
    the bank's billing defaults on `tenant` (migration `d5a7c9e1f3b4`, with RLS; existing
    Admin groups get `billing:read` and `billing:manage`);
  - **Metrics:** a catalog of 24 (`services/billing/metrics.py`);
  - **Pricing** (`pricing.py`): unit price, step tiers, minimum, negotiated price,
    discount, waived and exempt, all labelled with camt.086 charge-method and
    payment-method codes;
  - **Statements** (`statements.py`): one per customer plus the bank-wide one, with volumes
    per account;
  - **API** (`/api/v1/billing`): metrics, fee items (plus a starter schedule), bank
    settings, customer terms with overrides, statement preview, and `report.csv`.
  - **Tests:** 12 on SQLite, plus the Postgres run and an RLS test (only this bank's
    activity is counted).

