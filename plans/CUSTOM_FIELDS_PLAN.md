# Custom fields on banks, customers and accounts

The service provider and the banks need to hang their own identifiers and codes on OpenClear's
records, to tie them back to other systems. Examples from the product owner:
- the service provider puts an ERP id on each bank;
- a bank puts an account officer code on each customer;
- a bank puts an identifier on each account, to aggregate it in the core or another system.

What exists today: one fixed `external_customer_id` on customers and one `external_account_id` on
accounts (with import matching by it), and nothing on banks. Those stay; custom fields are for
everything else, and a custom field is never an import key.

## Decisions (the product owner, 2026-10-23 and 2026-10-28)

- **CF-D1. Who defines what.** The service provider defines fields on **banks**. Each bank defines
  its own fields on its **customers** and **accounts**. The provider doesn't define customer or
  account fields for every bank: that's the bank's data. A shared template for a new bank can come
  later.
- **CF-D2. Who sees the provider's bank fields.** Platform staff only, unless a field is marked
  *shown to the bank*, which shows it read-only in the bank's Settings. An ERP id or a contract
  reference is the provider's business.
- **CF-D3. Who sees a bank's fields.** The bank's staff. A field marked *shown to the customer*
  appears read-only to the customer's own people on its accounts; off by default, since an officer
  code is internal. Customers never edit them. Every read by a customer-scoped session, in a page,
  the API, an export or the exception page, goes through one function that joins through the
  customer-scoped repository and drops fields not shown to the customer, with a test for each place.
- **CF-D4. Types.** Text (with a maximum length, 255 at most), whole number, decimal
  (`Numeric(18, 6)`), date, yes/no, and a choice from a list. A text field can be *unique* (within
  the bank, or across banks for the provider's), enforced by a partial unique index, the database
  error turned into a plain message. There are no free patterns (regular expressions): a choice
  list or a length covers officer codes and ids without the risk of a slow pattern.
- **CF-D5. Required means required in forms.** A person can't save a customer or account in the
  web form without a required field. A write that doesn't mention a field, from the API, a bulk
  upload or the drop-box, leaves its value unchanged, or empty on a new record: the core's sync was
  written before the field existed, and a settings change must never break it. A filter on the
  customer and account lists, and a control check, show the records missing a required value, for
  the bank to fill in by hand or with the update file (CF-D10).
- **CF-D6. Where values appear.**
  - Forms and detail pages, every field.
  - The customer and account lists: fields marked *show in list*, at most three per list.
  - Search: text fields, through the one indexed column; filters: one custom field at a time.
  - The CSV and JSON exports, and the full data export.
  - The API: `custom_fields` on customers and accounts (CF-D8).
  - Bulk upload of accounts, as extra columns named by the field's key.
  - The exception page shows the account's and customer's fields in its account and customer
    details, so the person deciding sees, say, the officer code. They aren't a column in the
    exception list, which stays focused on deciding.
  - The provider's bank fields: the Platform pages. A platform API for them is deferred: there's
    no platform API for banks today, and a write scope on a platform key would be a first; it's
    designed with the ERP integration (`ERPNEXT_PLAN.md`) when that comes.
- **CF-D7. Where values travel with decisions.** A destination names the fields it wants, in
  order, and gets nothing else: a decision-file layout in its settings, a webhook endpoint on its
  own row, a feed consumer in its request (`?custom_fields=aggregation_id,…`). There's no
  per-field switch: one place to look and to audit, and a destination's output changes only when
  someone edits that destination. In the OpenClear CSV layout they go at the end in the order
  named; the NACHA layout has nowhere for them and ignores the setting; in JSON they're under one
  `custom_fields` object. An account's field is named by its key, its customer's as
  `customer.<key>`. *Amended 2026-10-28:* webhooks stay thin (ids only, as
  `services/webhook_service.py` promises, so a misconfigured endpoint can't leak data); a
  receiver reads the values from the feed with `?custom_fields=`.
- **CF-D8. Values are strings in the API,** in and out, formatted by type (an ISO date, decimal
  text, `true`/`false`); `GET /api/v1/custom-fields` gives each field's type, and the API docs and
  the SDK READMEs say consumers convert on their side. So `custom_fields` is a map from key to
  string, which the SDK generator types exactly, the same in CSV and JSON, and never changes shape.
- **CF-D9. Storage.**
  - **Definitions:** `custom_field` (bank fields: `tenant_id`, row-level security) and
    `platform_custom_field` for the provider's.
  - **Bank values:** one table, `custom_field_value` (`tenant_id`, row-level security), with a
    real foreign key to the customer or to the account, typed value columns, an index on
    (field, text value) for search, and the partial unique index for unique fields. Real foreign
    keys mean customer offboarding deletes the values through its schema walk, with no extra code.
  - **The provider's values on banks:** `platform_custom_field_value`, keyed `bank_id` (as
    `bank_deletion` is): no `tenant_id`, so no row-level security to let a bank's session read a
    hidden value, and the demo reset and bank offboarding don't wipe it. **They survive a bank's
    offboarding,** like its name and slug, and the certificate's "kept" sentence says so.
- **CF-D10. Bulk updates and automation,** so a bank can keep officer codes and the like current
  from its core or HR system.
  - *A field update file:* a CSV keyed by `account_number` or `customer_number` (or the external
    id) with a `cf_` column per field. It updates existing records only, never creates one. A blank
    cell leaves the value as it is; clearing values takes the file's option "blank cells clear
    values". Unknown records, and customers whose service has ended, are refused row by row on the
    usual results page; every value is checked against its field before any is saved.
  - *Automatic:* the same file through the drop-box import, in a `fields/` folder, with the same
    malware scan, signing and results history as the other imports.
  - *The API:* `custom_fields` on `PUT /api/v1/accounts/{account_number}` and
    `PUT /api/v1/customers/{customer_number}` (which already create or update), and a batch call of
    up to 1,000 records, `POST /api/v1/accounts/custom-fields` and the same for customers.
  - *Least privilege:* a new permission, `custom_field:write`, masked for customer-scoped
    sessions, lets an automation's API client set field values and nothing else about accounts or
    customers.
  - *Audit:* one action-log entry per file or batch, with its counts; each value's before and
    after in that upload's row results (a new column there). A value changed in a form or by one
    API call is an action-log entry with before and after: the first record edits with both.
    Values are data, not settings: no second approver.
- **CF-D11. Change control.**
  - Adding, changing or archiving a definition is a *sensitive* change, so it waits for a second
    administrator when the bank asks for that. The provider's definitions always need two
    operators.
  - A definition that has values is archived, not deleted: hidden from forms, still read
    everywhere, never accepted in writes.
  - A choice-list option in use is retired, not deleted: hidden from forms, kept on records.
- **CF-D12. Limits and care.** At most 25 fields per kind per bank. The help text says not to
  store taxpayer ids, account passwords or personal data that has no use here, and a CUEC asks the
  bank to keep to that.
- **CF-D13. Upgrade safety.** Nothing a bank or the service provider defines may break an upgrade
  or a consumer of OpenClear's output.
  - *Names:* always namespaced, under `custom_fields` in JSON and with a `cf_` prefix in CSV
    exports and upload columns, so a built-in field added later can never collide. A key is
    `[a-z][a-z0-9_]{0,39}`, fixed when the field is created; renaming changes only its label.
  - *Changing a field with values:* its type is fixed; *unique* can be switched on only after
    every existing value passes, with the failing records listed; a length can grow, not shrink.
  - *CSV:* every custom value written to any CSV (the exports, the feed, the OpenClear layout)
    goes through the same formula-prefix escaping as `list_search._cell` and the feed's
    `_csv_safe`, with a test: these are the first wholly user-typed values that reach a core's file.
  - *A built-in field replacing a custom one:* a migration helper moves the values; the custom
    field stays readable for at least one release, said in the CHANGELOG.
  - *Migrations:* additive only: new tables, and new nullable columns on `webhook_endpoint` (the
    keys it wants) and `bulk_upload_row_result` (before and after). A downgrade past this release
    drops the values, which the CHANGELOG says. `external_customer_id` and `external_account_id`
    stay as they are.
  - *Plugins:* custom fields are a separate attribute in what hooks receive, never merged into
    the record's own keys.
  - *Scale:* `tests/test_scale/test_custom_fields.py` seeds a bank with 25 fields on 20,000
    accounts and times the list, the search and the export.

## Stages

| Stage | What | Status |
|---|---|---|
| CF1 | Model, migration (RLS on the bank tables), the service: definitions, keys, validation by type, values, uniqueness, limits, archiving, retiring options; the customer-scoped read | Done |
| CF2 | The bank's Settings → Custom fields (two-person where the bank asks); customer and account forms, detail pages, *show in list*, search and the one filter, the missing-required filter and control check; the exception page | Done |
| CF3 | The provider's bank fields: Platform → Custom fields, each bank's page; *shown to the bank* in its Settings; survival at offboarding, in the certificate | Done |
| CF4 | API: `custom_fields` on customers and accounts, `GET /api/v1/custom-fields`; OpenAPI, the SDKs and their READMEs; the CSV and JSON exports and the data export; the account bulk upload's columns | Done |
| CF5 | Bulk updates (CF-D10): the update file, the drop-box `fields/` folder, the batch API, `custom_field:write`, before and after in row results | Done |
| CF6 | Decisions (CF-D7): the OpenClear CSV layout's setting, the feed's parameter (webhooks stay thin; see CF-D7); the CSV escaping everywhere | Done |
| CF7 | Docs: admin guide, data dictionary, threat model, CUEC, CHANGELOG; the scale test | Done |

One release once CF5–CF7 are done (decided 2026-10-28): CF1–CF4 merge first, unreleased.

## Progress log

- 2026-10-23: Plan written from the product owner's request.
- 2026-10-23: The product owner confirmed who sees what, and values everywhere a record is looked
  up or moved; the exception page shows them in its details; bulk updates for automation asked for.
- 2026-10-28: Reviewed against the code before building. **Findings:** *required* on every write
  would let a settings change break the core's sync; the per-field *include in decisions* flag had
  no home in the feed (consumers are cursor names) or webhooks (endpoints have only event types),
  and duplicated the destination's choice; the SDK generator can't type a mixed-value map; a
  `tenant_id` on the provider's values would give the bank's session a row-level policy to read
  them, and the demo reset and offboarding would wipe them; the lists have no column chooser.
  The product owner decided: required in forms only (CF-D5); strings in the API, consumers convert
  (CF-D8); destinations name their keys, no per-field flag (CF-D7); the provider's values survive
  offboarding (CF-D9); the platform API for bank fields deferred (CF-D6). Also fixed: *show in
  list* instead of a column chooser, text-only search and uniqueness, retiring choice options,
  CSV escaping for every output, before-and-after audit named as new, the scale test named.
- 2026-10-28: CF1 built: `domain/custom_field.py`, migration `c4d2e8f1a7b3`,
  `services/custom_field_service.py`, the data dictionary. **Findings:** a value on an account
  has to be limited by per-account access too (`db/account_scope.py`'s test caught it); and a key is
  exact, never lowercased, since it's the field's identity. Uniqueness is the database's: a text
  field's value is copied into `unique_text` while the field is unique, under a unique constraint,
  so two writers can't race past the check.
- 2026-10-28: CF2 built: Settings → Custom fields (`custom_field.save` and `custom_field.archive`,
  sensitive changes); the customer form, detail page and hub (columns for *show in list*, the
  search over text values, the missing-required filter); the account form, a Custom fields page per
  account, the list's columns and its filter by one field; the exception page's facts. The control
  check for missing required values waits for CF7, where its control and CUEC are defined (every
  live check must test a control).
- 2026-10-28: CF3 built: Platform → Custom fields (`platform.custom_field.save` and
  `platform.custom_field.archive`, always two operators), each bank's values page (one operator:
  values are data), the bank's read-only *From your service provider* section (a tile only while
  something is shown), and the certificate's "kept" sentence. **Finding:** a definition change
  that waited for approval wasn't checked until it was applied, so a bad key could sit under
  Changes and fail there; both kinds now check the input before queuing it.
- 2026-10-28: CF4 built: `custom_fields` on the account and customer read models and upserts (keys
  left out untouched, `null` clears, a customer-scoped caller is refused), `GET /api/v1/custom-fields`,
  OpenAPI and the three SDKs regenerated with a *Custom fields* note in each README, the data
  export's `custom_fields.json` and `custom_field_values.json` (a customer's export: only fields
  shown to it), and `cf_<key>` columns on the account bulk upload. There's no CSV export of
  accounts or customers to extend: the JSON API and the data export are the exports.
- 2026-10-28: CF5 built. Update files (web, `fields/accounts/` and `fields/customers/` drop folders,
  bank-wide only) and `POST /api/v1/{accounts,customers}/custom-fields` (at most 1,000, kept as a
  bulk upload and imported in the request) share one import path with `ImportSpec.updates`: nothing
  tracked for back-out, no per-row fixes, one audit entry per file. `set_values` now works in a
  savepoint, so one row's unique clash no longer rolls back the rest of an import. A blank cell
  in a file (pandas' NaN) was stored as "nan" by the CF4 account upload; fixed.
- 2026-10-28: CF6 built. `?custom_fields=` on `GET /api/v1/decisions` and `feed.csv`; the OpenClear
  CSV layout's "Custom fields to add" setting (checked on save, a sensitive change like the rest
  of Decision files) for the outbox and `GET /api/v1/decisions/file`. A customer-scoped reader
  gets only fields shown to the customer. The webhook endpoint keys were dropped: webhook
  payloads are ids only by design (CF-D7 amended), so no `webhook_endpoint` column.
- 2026-10-28: CF7 built. Control check `custom_fields` (nightly; warns while a record lacks a
  required value) under new control OC-SI-14, linked to CUEC B-SI-12-03; threat OC-T28 (bulk
  rewrites). Scale test `tests/test_scale/test_custom_fields.py`, opt-in with
  `OPENCLEAR_SCALE_TESTS=1`: 20,000 accounts x 25 fields seeds in 7 s; the list 0.14 s, a value
  filter 0.01 s, the missing-required filter 0.04 s (5.6 s before it moved into SQL), the API
  list of 500 0.28 s, a 1,000-row update file 0.8 s, the data export 12 s (SQLite, M-series Mac).
