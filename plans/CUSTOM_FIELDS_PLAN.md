# Custom fields on banks, customers and accounts

The service provider and the banks need to hang their own identifiers and codes on OpenClear's
records, to tie them back to other systems. Examples from the product owner:
- the service provider puts an ERP id on each bank;
- a bank puts an account officer code on each customer;
- a bank puts an identifier on each account, to aggregate it in the core or another system.

What exists today: one fixed `external_customer_id` on customers and one `external_account_id` on
accounts (with import matching by it), and nothing on banks. Those stay; custom fields are for
everything else.

## Decisions (the product owner, 2026-10-23: CF-D2, CF-D3 and CF-D5 as recommended)

- **CF-D1. Who defines what.** The service provider defines fields on **banks**. Each bank defines
  its own fields on its **customers** and **accounts**. The provider doesn't define customer or
  account fields for every bank: that's the bank's data. A shared template for a new bank can come
  later.
- **CF-D2. Who sees the provider's bank fields.** Platform staff only, unless a field is marked
  *shown to the bank*, which shows it read-only in the bank's Settings. An ERP id or a contract
  reference is the provider's business.
- **CF-D3. Who sees a bank's fields.** The bank's staff. A field marked *shown to the customer*
  appears read-only to the customer's own people on its accounts; off by default, since an officer
  code is internal. Customers never edit them.
- **CF-D4. Types.** Text (with a maximum length, 255 at most), whole number, decimal, date,
  yes/no, and a choice from a list. Each field can be *required* and, for identifiers, *unique*
  (within the bank, or across banks for the provider's). There are no free patterns (regular
  expressions): a choice list or a length covers officer codes and ids without the risk of a slow
  pattern.
- **CF-D5. Where values appear.** Both of these, together:
  - **Every field**, with no setting: forms, detail pages, and an optional column in the
    customer and account lists.
  - List search and filters.
  - The CSV and JSON exports, and the full data export.
  - The API: `custom_fields` on customers and accounts; bank fields on the platform API.
  - Bulk upload of customers and accounts, as extra columns named by the field's key.
  - **A field marked *include in decisions*** (off by default) is also sent with each decision
    in the decision feed, decision files and the `exception.decided` webhook, wherever that
    destination has opted in (CF-D9). That's what makes an account's aggregation id useful to the
    core.
  - **The exception page** shows the account's and customer's fields in its account and customer
    details, so the person deciding sees, say, the officer code. They aren't a column in the
    exception list, which stays focused on deciding.
- **CF-D6. Storage.**
  - **Definitions:** `custom_field` (bank fields: `tenant_id` and row-level security), and
    `platform_custom_field` for the provider's.
  - **Bank values:** one table, `custom_field_value`, with a real foreign key to the customer or
    to the account, typed value columns, and a unique index for unique fields.
  - **The provider's values on banks:** `platform_custom_field_value`, apart from the bank's
    tables, so a bank's session can never read a hidden one.
  - Real foreign keys mean customer offboarding deletes the values through the same schema walk,
    with no extra code.
- **CF-D7. Change control.**
  - Adding, changing or archiving a definition is a *sensitive* change, so it waits for a second
    administrator when the bank asks for that. The provider's definitions always need two
    operators.
  - A definition that has values is archived, not deleted: hidden from forms and kept in exports.
  - Each value change is in the action log with before and after, like any other edit.
- **CF-D8. Limits and care.** At most 25 fields per kind per bank. The help text says not to store
  taxpayer ids, account passwords or personal data that has no use here, and a CUEC asks the bank
  to keep to that.
- **CF-D9. Upgrade safety.** Nothing a bank or the service provider defines may break an upgrade
  or a consumer of OpenClear's output.
  - *Decision files to the core:* custom fields go only to a destination that opts in, per
    decision-file layout or feed consumer, never because a field is flagged. In CSV they go at the
    end in a fixed order; in fixed-width only where a layout places them; in JSON and the webhook
    under one `custom_fields` object.
  - *Names:* always namespaced, under `custom_fields` in JSON and with a `cf_` prefix in CSV
    exports and upload columns, so a built-in field added later can never collide. A field's key
    is fixed when it's created; renaming changes only its label.
  - *Changing a field with values:* its type is fixed; *required* and *unique* can be switched on
    only after every existing value passes, with the failing records listed; a length can grow,
    not shrink.
  - *The API:* `custom_fields` is a map from key to a string, number, yes/no or ISO date, fixed
    in OpenAPI and the SDKs, and only ever added to.
  - *A built-in field replacing a custom one:* a migration helper moves the values; the custom
    field stays readable for at least one release, said in the CHANGELOG.
  - *Migrations:* additive only (new tables). A downgrade past this release drops the values,
    which the CHANGELOG says. `external_customer_id` and `external_account_id` stay as they are.
  - *Plugins:* custom fields are a separate attribute in what hooks receive, never merged into
    the record's own keys.
  - *Scale:* the value table is indexed by field and value; a large bank is in the scale tests.
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
  - *Least privilege:* a new permission, `custom_field:write`, lets an automation's API client set
    field values and nothing else about accounts or customers.
  - *Audit:* one action-log entry per file or batch, with its counts; each value's before and
    after in that upload's results. Values are data, not settings: no second approver.

## Stages

| Stage | What |
|---|---|
| CF1 | Model, migration (RLS on bank tables), the service: definitions, validation, values, uniqueness, limits, archiving |
| CF2 | The bank's Settings → Custom fields (two-person where the bank asks); customer and account forms, detail pages, list columns, search |
| CF3 | The provider's bank fields: Platform → Custom fields, each bank's page; *shown to the bank* in its Settings |
| CF4 | API (customers, accounts, platform banks), OpenAPI and the SDKs; exports and the data export; bulk upload columns |
| CF5 | Bulk updates (CF-D10): the field update file, the drop-box `fields/` folder, the batch API, `custom_field:write` |
| CF6 | *Include in decisions* with each destination's opt-in (CF-D9): the decision feed, decision files, the webhook; the exception page |
| CF7 | Docs: admin guide, data dictionary, threat model, CUEC, CHANGELOG; tests, including RLS, a customer's view, upgrade safety and a large bank |

## Progress log

- 2026-10-23: Plan written from the product owner's request.
- 2026-10-23: The product owner confirmed CF-D2, CF-D3 and CF-D5 (every field everywhere it's
  looked up or moved, and *include in decisions* per field). Open: values on the exception page
  (recommended: in its account and customer details, not as a list column).
- 2026-10-23: The product owner chose the exception page as recommended, and asked for bulk
  updates for automation (CF-D10). Upgrade safety recorded (CF-D9); it makes *include in
  decisions* an opt-in per destination. Ready to build from CF1.
