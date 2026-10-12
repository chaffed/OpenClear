# Reporting plan

Reports at three levels: the **operator** who runs an installation, each **bank**, and each
bank's **business customers**. People build their own reports in the browser, save and share
them, run them on a schedule, and export them as CSV, Excel or PDF. What a person can put in a
report is exactly what they're allowed to see: for a customer's people, only the accounts
they've been given, and only the kinds of data their permissions cover.

**Status: replaced.** On 2026-10-12 the product owner decided OpenClear won't have a reporting
dashboard or report builder of its own. Banks report from their own business intelligence
platform, fed from OpenClear's API; the guide is [docs/guides/bi.md](docs/guides/bi.md). The
analysis below is kept for the record: it's why the data sits behind the API and its
permissions rather than being read straight from the database.

## The choice: an outside BI tool, or a report builder inside OpenClear

| Option | Licence | Fit for three tiers with per-account, per-permission data |
|---|---|---|
| **Apache Superset**, embedded with guest tokens | Apache 2.0 | Strong charts and dashboards. A guest token carries row-level filters as SQL clauses ([embedding docs](https://superset.apache.org/user-docs/using-superset/embedding)), so per-account rows are possible. But it reads the database directly, so OpenClear's own permission and scoping rules would have to be rewritten as SQL clauses and kept in step forever; column-level limits (no payee for someone without the right permission) are awkward; and it's a second application to run (its own metadata database, Redis and workers). Heavy for a sandbox or a single-bank install. |
| **Metabase** | AGPL | Easy to use, but row-level permissions ("data sandboxes") are only in the paid Pro and Enterprise editions ([Metabase docs](https://www.metabase.com/docs/latest/permissions/data)). The open-source edition can't keep one customer's rows from another's. Ruled out for the bank and customer tiers. |
| **ReportBro** (designer plus Python library) | AGPL, or commercial ([reportbro-lib](https://github.com/jobsta/reportbro-lib)) | A browser designer for laid-out PDF and Excel reports, rendered by a Python library: it fits OpenClear's stack and licence (OpenClear is AGPL). It formats data; it doesn't decide what data a person may see, which is what OpenClear must do anyway. |
| **A builder inside OpenClear** | Ours | Uses the permission checks, customer scoping and Postgres row-level security OpenClear already enforces everywhere, through the same code the lists use (`web/list_search.py`). One place decides what anyone can see. |

**Recommendation:** build the report builder inside OpenClear, on a set of report **datasets**
that carry their own security rules, and use **ReportBro** for designed PDF layouts (branded
statements, monthly summaries). Keep Superset as an optional add-on for the operator's own
analytics later, pointed at a read replica, never at bank or customer data directly.

The deciding reason: in a bank product the hard part of reporting isn't the charts, it's never
showing one customer another's data, and never letting a report reveal more than the person's
screens do. That logic already lives in OpenClear; a BI tool would need a second copy of it.

## How it works

### Datasets: the one security boundary

A report is built on a **dataset**: a named, documented set of columns OpenClear defines (for
example "Presented checks and decisions"). Each dataset says:

- **its tier**: operator, bank or customer;
- **its row scope**: the bank, then the customer, then the accounts the person may see (below);
- **for each column, the permission it needs**: a column of payees needs `issued_item:read`, a
  decision's maker and checker need `exception:read`, the action log needs `audit_log:read`;
- **what may be filtered, grouped, summed and sorted**, and the largest number of rows.

Every run, preview, export and scheduled delivery goes through the same check at the time it
runs, for the person it runs for. A saved report shared with someone who lacks a column loses
that column for them, and says so; a report whose dataset they can't use at all won't run.

### Per-account access for customers' people (new)

Today a customer's people see every account of their customer. The plan adds **account access
on each customer membership**: all of the customer's accounts (today's behaviour, the
default), or a chosen list. It's managed by the bank, and by the customer's own admin for its
own people.

**It applies everywhere, not just in reports**: the lists, exceptions, uploads, the API and
reports all use it. A report that showed less than the screens, or more, would be a bug. The
customer scope in the repositories (`CustomerScopedRepository`) gains an account filter; the
places that already filter by customer get it for free.

### Permissions decide the columns

| Permission | Data it opens in reports |
|---|---|
| `account:read` | Accounts, balances of activity per account |
| `issued_item:read` | Issued checks, payees, voids, issue files and their layouts |
| `paid_item:read`, `check_image:read` | Presented checks; image links (never images in an export) |
| `exception:read` | Exceptions, decisions, who recommended and decided, return reasons, deadlines |
| `ach_authorization:read`, `ach_transaction:read` | ACH filters, debits and credits, ACH returns |
| `stop_payment:read` | Stop payments |
| `wsud:sign` / `wsud:read` | Written statements of unauthorized debit (own / all customers) |
| `billing:read` | Billing statements and fees |
| `audit_log:read` (bank only) | The action log, access recertification, support access |
| `user:manage` (bank only) | Users, groups and sign-in activity |

### The three tiers

| Tier | Who | Datasets (first version) | Never |
|---|---|---|---|
| **Operator** | Platform operators (`/ui/platform`) | Per-bank activity and volumes, timeliness, jobs and failures, controls over time, security-event delivery, usage for billing | Any bank's account-level data, payees, amounts per item or people; per-bank detail only through support access the bank grants |
| **Bank** | Bank staff, by permission | Exceptions and decisions, presented and issued items, ACH, stop payments, return reasons, customer activity (the hub over time), timeliness, users and access, the action log, billing | Other banks |
| **Customer** | A customer's own people, by permission and account access | Their issued checks, presented checks and decisions, ACH activity, stop payments, their billing statement | Other customers; accounts not given to them; columns their permissions don't cover |

### The builder

Server-rendered, like the rest of OpenClear, with a little script:

1. Choose a dataset (only those the person may use).
2. Choose columns, filters (date ranges relative to today, account, status, amount), grouping
   and totals, and sort. A preview shows the first rows as it's built.
3. Save it, privately or shared with the person's own organization (a bank's staff, or a
   customer's people). Everyone it's shared with sees only what they may see.
4. Run it now, or **schedule** it (daily, weekly, monthly). A scheduled report is delivered as
   a link that needs signing in, never as an attachment, so it can't be forwarded.
5. Export as CSV, Excel, or PDF. Big runs are background jobs (`services/long_runs.py`).

A library of ready-made reports for each tier (for example "Decisions this month by customer",
"Items returned and why", "Items decided automatically at the deadline", "Customers who didn't
send an issue file this week") gives everyone a starting point.

**Designed PDF reports** (a branded monthly statement, say) use ReportBro: a bank's admin lays
out a template in the designer once; reports fill it with a dataset's rows.

### Safety

- Every run and export is in the action log and is a security event (`report.exported`), with
  the dataset, columns and row count, never the data.
- Spreadsheet formula injection is neutralized in CSV and Excel (as the lists already do).
- Row caps per tier, query time limits, and background jobs for large exports.
- Postgres row-level security still applies underneath: a dataset can't read another bank's
  rows even if its own filter were wrong.
- The evidence export lists the reports run in the period.

## Phases

| # | What | Notes |
|---|---|---|
| R1 | **Datasets and the engine.** The dataset definitions with per-column permissions, the run-time check, exports through background jobs, the security events. Bank tier first, with five datasets. | Builds on `web/list_search.py`'s query specs. |
| R2 | **Per-account access** for customer memberships, applied across lists, exceptions, uploads, the API and reports, with its screens for bank and customer admins. | One migration; a guard test that every customer-scoped query applies it. |
| R3 | **The builder**, saved and shared reports, the ready-made library; the customer tier. | Its tile in the control panel at bank and customer level. |
| R4 | **Schedules** (delivered as links), and **designed PDF reports** with ReportBro. | ReportBro's designer vendored under `static/`, under the strict CSP. |
| R5 | **The operator tier**: aggregates across banks on the operator view. Optionally, Superset against a read replica for the operator's own analytics. | No bank-level detail without support access. |

Fitting MASTER_PLAN.md: **M8** = R1 + R2 (the security foundations, one migration), **M9** =
R3 + R4 + R5, then **release 1.13**.

## Testing

- For every dataset and every column: a person without the column's permission never gets it,
  in a preview, a run, an export or a schedule.
- For every customer-scoped dataset: a customer's person sees only their accounts, and nothing
  of another customer's, on SQLite and on Postgres with row-level security.
- A shared report run by someone with fewer rights shows less, and says what was left out.
- Per-account access holds across every list and the API, not just reports (a guard test).
- Exports: formula injection neutralized; row caps; large runs as jobs; action-log entries.
- Accessibility (axe) and phone width for the builder and the report pages.

## Decisions (proposed; say if you'd like any changed)

| # | Question | Proposal |
|---|---|---|
| RP-D1 | Outside BI tool or our own builder? | Our own builder on datasets, ReportBro for designed PDFs; Superset only as an optional operator add-on. |
| RP-D2 | Per-account access: reports only, or everywhere? | Everywhere. Reports and screens must agree. |
| RP-D3 | Scheduled reports by email attachment? | No: a link that needs signing in. |
| RP-D4 | Can a customer's admin limit their own people to some accounts? | Yes, within the customer's own accounts; the bank can too. |
| RP-D5 | Operator reports on bank detail? | No: aggregates only; detail through the bank's support access grant. |
| RP-D6 | Charts? | Simple bar and line charts in the builder (rendered server-side as SVG, no chart library from a CDN); dashboards later if asked. |

## Progress log

(Newest last.)
- 2026-10-12 Plan written. Options reviewed: Superset (embedding with guest-token row filters),
  Metabase (row-level permissions only in paid editions), ReportBro (AGPL, fits the stack).
  Recommendation: our own builder on secured datasets, with ReportBro for designed PDFs.
- 2026-10-12 Put on hold by the product owner: reporting comes after every other feature.
- 2026-10-12 Replaced by the product owner: no reporting dashboard in OpenClear. Banks use their own BI platform, fed through the API (docs/guides/bi.md: credentials, what to pull and how to keep it current, the data model, row-level security for bank, customer and operator, reading a replica on a self-hosted install). Gaps that would make BI pipelines easier, not built: a "changed since" filter on the lists, read-only API credentials scoped to one customer, and the full data export through the API.

