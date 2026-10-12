# Reporting with your BI platform

OpenClear doesn't have a reporting dashboard of its own. Banks already run a business
intelligence platform (Power BI, Tableau, Looker, Qlik, or an open-source one such as Superset
or Metabase) on a data warehouse, with their own governance around it. This guide is for the
bank's data and BI team: how to get OpenClear's data into that platform, keep it current, and
keep the same access rules there that OpenClear enforces.

## The short version

1. Create an **API client** in OpenClear for your data pipeline, in a security group that can
   only read.
2. Have your pipeline (Azure Data Factory, Fabric, Airbyte, Fivetran, dbt with a loader, or a
   small script) pull from OpenClear's API on a schedule into your **warehouse**.
3. Pull the **feeds** (decisions, the action log, security events) incrementally, and the
   **lists** (exceptions, items, ACH, accounts, customers) in full or by what the action log
   says changed.
4. Point your BI platform at the warehouse, and apply **row-level security** there: by bank
   (if you're the operator), by customer, and by account.

The API is the stable contract between OpenClear and your warehouse: it's versioned (`/api/v1`),
changes only by adding, and is described in `openapi/openclear-v1.json`. The database schema
isn't a contract and changes between releases.

## Ways in

| Way | Use it for | Notes |
|---|---|---|
| **The API** (recommended) | Everything, on every kind of installation | OAuth 2.0 client credentials; JSON; paged lists and cursor feeds. |
| **File exports** | A first load, an audit, a one-off | The decision feed as CSV, billing CSVs and ISO 20022 statements, any list's CSV export from the web, and the full data export (Settings → Data export: every record as JSON in a zip). |
| **A read-only database replica** | Only a bank running its own OpenClear, with a database team who'll track schema changes | Never on a hosted or shared installation. See "Reading the database directly" below. |

Connecting a BI tool **directly** to the API works for small volumes (Power BI's Web
connector, a Tableau web data connector), but scheduled refreshes, paging and token handling are
easier in a pipeline that lands the data in your warehouse first. The rest of this guide
assumes a pipeline.

## Credentials

1. In OpenClear, **Settings → Security groups**: create a group, for example "BI read-only", with
   only the read permissions you'll pull: `account:read`, `issued_item:read`, `paid_item:read`,
   `stop_payment:read`, `exception:read`, `ach_authorization:read`, `ach_transaction:read`, plus
   `audit_log:read` for the action log and security events, and `billing:read` for billing.
   Leave out everything that writes or manages.
2. **Settings → API clients**: create a client in that group. Copy its secret once into your
   pipeline's secret store (Azure Key Vault, AWS Secrets Manager, and so on).
3. The pipeline gets a token, then calls the API with it:

```sh
curl -s https://openclear.bank.example/api/v1/oauth/token \
  -d grant_type=client_credentials -d client_id=$CLIENT_ID -d client_secret=$CLIENT_SECRET
# {"access_token": "...", "token_type": "Bearer", "expires_in": ...}
curl -s https://openclear.bank.example/api/v1/exceptions?limit=1000 -H "Authorization: Bearer $TOKEN"
```

Rotate the secret on your usual schedule (Settings → API clients → Rotate). The control
"Integration secrets rotated" warns when a secret is over a year old. Every token and every
export is recorded as a security event, so your SOC sees the pipeline's activity.

## What to pull

| Data | Endpoint | How to keep it current |
|---|---|---|
| Decisions (pay or return, who recommended and decided, when) | `GET /api/v1/decisions?after=<sequence>` (JSON), or `/api/v1/decisions/feed.csv` | **Incremental**: keep the last `sequence` and pass it as `after`. `X-Latest-Sequence` says how far there is to go; a `Link: rel="next"` header gives the next page. |
| The action log (every change, by whom, signed) | `GET /api/v1/audit-log?after_sequence=<n>` | **Incremental**, by `sequence`. Also tells you what changed elsewhere (see below). |
| Security events | `GET /api/v1/security-events?after=<n>` | **Incremental**, by `sequence`. |
| Exceptions | `GET /api/v1/exceptions?limit=1000&offset=…` | Full load, or refresh the ones the action log names. |
| Issued checks, presented checks, stop payments | `/api/v1/issued-items`, `/api/v1/paid-items`, `/api/v1/stop-payments` | The same. |
| ACH filters and transactions | `/api/v1/ach/authorizations`, `/api/v1/ach/transactions` | The same. |
| Accounts and customers | `/api/v1/accounts`, `/api/v1/customers` | Full load (small). |
| Users and their access (for access reviews) | `/api/v1/users` | Full load. |
| Billing | `/api/v1/billing/statements?period=YYYY-MM`, `/api/v1/billing/report.csv` | Per period, after it's finalized. |
| Controls | `/api/v1/controls/status` | A daily snapshot, to chart controls over time. |

**Paging.** Lists take `limit` (up to 1,000) and `offset`, return oldest first, and send
`X-Total-Count` and a `Link: rel="next"` header while there's more. Feeds take a cursor
(`after` or `after_sequence`) instead, which never skips or repeats a row.

**Keeping lists current without reloading everything.** The lists don't filter by "changed
since" yet. For a small or mid-sized bank a nightly full load is simplest. For a large one,
read the action log incrementally: each entry has `resource_type` and `resource_id`
(for example `issued_item` and its id), so the pipeline fetches only the records that changed:
`GET /api/v1/issued-items/{id}`, `/paid-items/{id}`, `/exceptions/{id}` and
`/ach/transactions/{id}`. Stop payments, accounts and customers are small enough to reload.

**Rate limits.** An API client is limited as itself (600 requests a minute by default). Pull
1,000 rows a page, run during quiet hours, and on a `429` wait for `Retry-After`.

## The data model

- Every record has a UUID `id`. Times are UTC, ISO 8601. Amounts are decimal strings: load them
  as `DECIMAL(15,2)`, never as floating point.
- An **exception** points at what it's about: `source_item_id` is a presented check
  (`paid-items`) or an ACH transaction, depending on `network_code` (`check` or `ach`).
- A **decision** points at its exception (`exception_item_id`) and records who recommended it,
  who decided it, the outcome, the reason, and whether it was decided automatically at the
  deadline.
- An **account** belongs to a **customer** (`customer_id`), or to the bank itself when that's
  empty. Items and exceptions carry `customer_id` too, for filtering.
- The full list of tables and columns is in the in-app Admin documentation, under Data
  dictionary.

A star schema that works well: a fact table of decisions (with exception and item details
joined in), a fact table of presented items, dimensions for date, account, customer, network,
outcome and reason, and the action log and security events as their own facts.

## Three levels of reporting, three sets of rules

What OpenClear enforces stops at the API. Once data is in your warehouse, your BI platform's
own access rules decide who sees what, so set them up to match.

### The bank

The API client above sees the whole bank, within its group's permissions. In the BI platform,
give each audience only what OpenClear would show them:

- staff who review exceptions: exceptions and decisions, not the action log;
- risk, compliance and audit: the action log, security events, controls and users;
- finance: billing.

### The bank's customers

Customers don't have API credentials of their own; the bank serves them their reports. Two
ways:

- **From your warehouse**, with row-level security on `customer_id` (and on account, if some
  of a customer's people may see only some accounts). Power BI: an RLS role filtering on the
  customer and account dimensions, mapped to the signed-in user. Tableau: user filters or
  entitlement tables. Looker: `access_filter` on a user attribute.
- **Through OpenClear itself, as the person**: an API client allowed to act for users
  (Settings → API clients → "May act for your users") can call the API with
  `OpenClear-Act-As: <the person's email>`. Each call then sees exactly what that person may
  see in OpenClear: their customer only, and only what their permissions allow. That suits
  reports generated per customer, for example into online banking.

A customer's own people can also export any list they can see as CSV from OpenClear,
and the bank can produce one customer's full data export from that customer's page.

### The operator (a hosted installation)

Whoever runs an installation for several banks reports on it with a **platform API key**
(`scripts/create_metering_api_key.py`, with the scopes it needs):

| Scope | Endpoints |
|---|---|
| `usage` | `GET /api/v1/platform/usage`: each bank's activity for billing |
| `billing` | `GET /api/v1/platform/billing/invoices` (JSON, CSV, ISO 20022) |
| `operations` | `GET /api/v1/platform/jobs`, `/jobs/summary`, `/platform/security-events` |

These are totals and operational data, never a bank's accounts, items or people. A bank's own
data stays with the bank: the operator sees it only through support access the bank grants.

## Reading the database directly (self-hosted only)

A bank running its own OpenClear may prefer to replicate its database into the warehouse
(change data capture with Debezium, AWS DMS, Fivetran's database connectors). If you do:

- Read from a **replica**, never the primary, with a **read-only role**.
- On Postgres, OpenClear's tables use **row-level security**: a session sees a bank's rows only
  after `SET app.current_tenant_id = '<the bank's id>'`. A single-bank install sets it once;
  never run a shared installation's database into one bank's warehouse.
- Leave out secrets: `sso_connection` client secrets and keys, `webhook_endpoint.secret_encrypted`,
  `api_client` secret hashes, `security_event_destination.token_encrypted`, `user.hashed_password`
  and `tenant_extension.config_encrypted`. Leave out check images (`check_image`) unless you
  need them; they're large and sensitive.
- The schema changes between releases (each release's migrations are in `migrations/versions/`
  and listed in the changelog). Re-check your pipeline at every upgrade; the API doesn't have
  this problem.

## Security checklist

- [ ] The pipeline's API client is in a read-only group with only the permissions it needs.
- [ ] Its secret lives in a secret store, and is rotated.
- [ ] Warehouse tables holding OpenClear data are classified as containing customer account
      information, and encrypted at rest.
- [ ] Row-level security in the BI platform matches OpenClear's: bank, customer, account, and
      permission (who may see payees, decisions, the action log).
- [ ] Exports from the BI platform are controlled the way the bank controls any account data.
- [ ] The SOC sees the pipeline's activity in OpenClear's security events (see the
      [SIEM guide](siem.md)).

## A minimal pipeline, as a script

A starting point in Python that loads the decision feed incrementally into a CSV file; swap the
file for your warehouse's loader.

```python
import csv, json, os, pathlib, httpx

BASE = "https://openclear.bank.example"
STATE = pathlib.Path("decisions.cursor")

token = httpx.post(f"{BASE}/api/v1/oauth/token", data={
    "grant_type": "client_credentials", "client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"],
}).json()["access_token"]
after = int(STATE.read_text()) if STATE.exists() else 0

with httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=60) as api, \
        open("decisions.csv", "a", newline="") as out:
    writer = None
    while True:
        page = api.get("/api/v1/decisions", params={"after": after, "limit": 1000})
        page.raise_for_status()
        rows = page.json()
        if not rows:
            break
        for row in rows:
            flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
            writer = writer or csv.DictWriter(out, fieldnames=list(flat))
            writer.writerow(flat)
        after = rows[-1]["sequence"]
        STATE.write_text(str(after))
```
