# Simplify plan: the bank's own WSUD form, and an advisory fraud score

The product owner's decisions on `FULL_REVIEW_PLAN.md` FR-D19 (2026-10-21):

- **WSUD (item 10):** remove OpenClear's Written Statement of Unauthorized Debit signing. Getting
  and keeping the statement becomes the bank's job, done its own way. OpenClear shows each bank's
  own form to its customers, written in Markdown, written in HTML, or uploaded as a PDF.
- **ML (item 12):** the fraud score is advisory only: it never decides an item. Customer-level
  models go. With the review's recommendation accepted, the shared cross-bank model and the
  fraud-training examples go too. Each bank has at most its own model, and scoring is off until
  the bank turns it on.
- **Billing (item 11)** is frozen, **SQL Server (item 13)** stays.

There are no active installations, so the migrations drop what's removed rather than archiving it.

## Decisions

- **SP-D1. One table for a bank's forms: `bank_form`.** Columns: `tenant_id`, `key` (`wsud` for
  now), `format` (`markdown`, `html` or `pdf`), `body` (Markdown or HTML), `storage_ref`,
  `filename`, `updated_by_user_id`, `updated_at`. It has row-level security. It's keyed so a
  second form needs no new table.
- **SP-D2. How each format is shown.**
  - **Markdown** is rendered and **HTML** is sanitised, both through the same nh3 allowlist as
    the banner (`render_markdown`); HTML gets no scripts, styles or event handlers.
  - **PDF** is uploaded through the usual malware check and accepted only if it starts with
    `%PDF-`, at up to 10 MB. It's stored with `storage.safe_name`, and the customer downloads it
    as `application/pdf` with `Content-Disposition: attachment`.
  - A bank can keep a short Markdown note above its PDF ("print it, sign it, return it to…").
- **SP-D3. Who sees it.**
  - A customer's people with `ach_transaction:read` see **Unauthorized debit statement** in the
    navigation when their bank has a form.
  - With no form there's no entry. The ACH help says to contact the bank, with its support
    contact.
  - The bank sets the form under Settings → Data & compliance → *Unauthorized debit statement*
    (`tenant:manage`). It isn't a protected change, since it moves nothing. It's audited as
    `bank_form.update`.
- **SP-D4. The obligation moves to the bank.**
  - A new bank CUEC, B-IR-6-02 *Obtain and keep Written Statements of Unauthorized Debit*,
    replaces the customer CUEC's "OpenClear collects and keeps the signed statement".
  - C-IR-6-01 becomes "report suspected fraud at once, and complete your bank's statement",
    *attested*.
  - Control OC-SI-07 and its check `wsud_signed` go. A new check, `wsud_form`, warns while a
    bank that has returned a consumer ACH debit in the last 60 days has no form published.
  - `docs/compliance/nacha.md` says the bank collects the statement.
- **SP-D5. What goes with WSUD signing:**
  - the `wsud_statement` and `wsud_statement_transaction` tables;
  - the WSUD signing key pair and its checks in `assert_production_safe`;
  - the two legal-text settings;
  - the `wsud:sign` and `wsud:read` permissions, which the migration strips from every security
    group;
  - the billing metric `wsud_statements`.

  Unknown `OPENCLEAR_WSUD_*` environment variables are ignored, as pydantic-settings does.
- **SP-D6. The score never decides.**
  - `DispositionMode.ML_DETERMINED` goes. Settings using it become `none` (items stay open), and
    `customer.disposition` loosens only for `fixed_pay`.
  - The score shows on the item and in the list, labelled advisory.
- **SP-D7. No customer models.**
  - `customer_ml_setting` goes, along with `ml_model` rows for a customer, per-customer training
    in `retrain_job`, the customer ML page's model and scoring cards, and `ml/predict.py`'s
    customer resolution.
  - The customer's page keeps its default-decision card, which is now the whole page, titled
    *Default decision*.
- **SP-D8. No shared model.**
  - `MlModelSource`, `tenant.ml_model_source` and its switch go, along with the shared slot in
    `ml_model` (rows with no bank), the platform's ML API (`/api/v1/platform/ml/*`) and the
    `shared_model` platform key scope.
  - `ml_new_bank_private_switch_lock_days` and the disclosure text go.
  - A bank's model trains only on its own live decisions once it has
    `ml_bank_model_min_decisions` (200).
- **SP-D9. No fraud-training examples.**
  - The routes, pages, API, service, bulk upload kind and `ml_training_example:write` go.
  - Existing training examples go too: their exceptions, decisions, synthetic items and upload
    files, and exception_item's training columns. (Changed while building SP2: there are no
    installations, and keeping rows nothing can show or use only kept dead code alive.)
- **SP-D10. Scoring is a bank switch, off by default.** `tenant.ml_scoring_enabled`, a control
  panel tile (*Fraud scoring*), a protected `sensitive` change. With it off, nothing is trained
  or scored, and the score's column and badges don't show.
- **SP-D11. Model risk stays, for the bank's own model:** replay and anomaly capture. Anomaly
  capture tests the matching rules, not the model, so it runs whether scoring is on or off.

## Stages

Each stage is a commit on `product-simplify`, with the tests, OpenAPI and SDKs, data dictionary,
CHANGELOG, docs and compliance crosswalks it touches. One migration each for SP1 and SP2.

| Stage | What | Decisions |
|---|---|---|
| **SP1. The bank's WSUD form** | `bank_form` with RLS, the settings tile and page, the customer's page, PDF download, CUEC and control changes, removal of signing (tables, keys, settings, permissions, billing metric) | SP-D1 to SP-D5 |
| **SP2. An advisory score, the bank's own** | `ML_DETERMINED`, customer models, the shared model and fraud-training examples removed; `ml_scoring_enabled` and its tile; training and scoring by bank only; model risk on the bank's model; the platform ML API removed | SP-D6 to SP-D11 |

## Progress log

- 2026-10-21: Plan written from the product owner's answers to FULL_REVIEW_PLAN.md FR-D19.
- 2026-10-21: SP1 built: `bank_form` (migration `3971bd3709a0`, RLS), `services/bank_form_service.py`,
  Settings → *Unauthorized debit statement*, `/ui/wsud` and its PDF download, `clean_html`; CUEC
  B-IR-6-02 added and C-IR-6-01 attested; OC-SI-07 now checks `wsud_form`; WSUD signing removed
  (tables, keys, legal-text settings, permissions, billing metric, docs). **Change from SP-D3:**
  the navigation entry shows to every customer person with `ach_transaction:read`, whether or not
  the bank has a form (the page then says to contact the bank), so no page pays a query to decide.
  Also removed: a `dev_keys/dev_keys` symlink an FR2 worktree committed (it's in PR #31's history).
- 2026-10-21: SP2 built (migration `5b8e2d4f6a1c`): `tenant.ml_scoring_enabled` and Settings →
  *Fraud scoring* (protected change `ml_scoring`); training, scoring and the ML admin pages and
  API by bank only; `ml_model` has row-level security now that every model has a bank; the
  customer page is *Default decision*; the shared model, customer models, `ML_DETERMINED` and
  the decision source `auto_ml`,
  fraud-training examples (and their rows, **change from SP-D9**), the platform ML API, the
  `shared_model` key scope, control OC-AC-11 / CUEC B-AC-21-01 and the billing metric
  `customer_model_monthly` removed. The demo bank turns scoring on and trains its own model.
