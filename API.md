# OpenClear REST API

All endpoints are prefixed with `/api/v1`. This is the JSON API surface — it exists
alongside (and, where equivalent, is backed by the same services as) the server-rendered
`/ui/*` web app, but the two are separate: the web app uses cookie sessions, the API
below uses JWT bearer tokens.

**SDKs:** [Python](sdks/python) (`openclear-sdk`) and [TypeScript](sdks/typescript) (`@openclear/sdk`), Apache-2.0. Both are generated from the contract below, with a hand-written layer for tokens, retries with idempotency keys, paging, bulk, the decision feed and webhook verification.

The full machine-readable contract is [`openapi/openclear-v1.json`](openapi/openclear-v1.json)
(OpenAPI 3.1), regenerated with `python scripts/export_openapi.py`. `/api/v1` only changes
additively: endpoints, optional parameters and response fields can be added, but nothing
is removed, renamed or made newly required. `tests/test_openapi_contract.py` enforces
that on every test run.

## Authentication

### Login

```
POST /api/v1/auth/login
```

Body (`LoginRequest`):

| field | type | notes |
|---|---|---|
| `tenant_slug` | string | which organization to log into |
| `email` | string | |
| `password` | string | |

Response (`LoginResponse`):

```json
{
  "mfa_required": false,
  "access_token": "...",
  "refresh_token": "...",
  "mfa_token": null,
  "token_type": "bearer"
}
```

If the account has a registered WebAuthn credential, `mfa_required` is `true` and
`access_token`/`refresh_token` are withheld — only `mfa_token` is returned. `mfa_token`
carries no permissions and can only be used against the two `/auth/webauthn/login/*`
endpoints below to complete the second factor.

### Refresh

```
POST /api/v1/auth/refresh
```

Body: `{"refresh_token": "..."}`. Returns a new `TokenResponse` (`access_token` +
`refresh_token`). The new tokens always reflect the membership's *current* security
group and active status — not whatever was true when the refresh token was issued — so a
permission change or deactivation takes effect the next time a client refreshes.

### API clients (for a bank's systems)

A bank's own systems (its core, its online banking provider, its middleware) sign in with
**OAuth 2.0 client credentials** instead of a person's password. A bank admin creates an
API client under Admin → API clients, choosing a security group (what it may do) and
optionally a customer (whose data it may see), and copies the secret, shown once.

```
POST /api/v1/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=occ_...&client_secret=ocs_...
```

The id and secret can also go in HTTP Basic auth.

**Signing in with a key pair instead (`private_key_jwt`, RFC 7523).** Under Admin → API
clients, **Use a key pair** registers the client's public key (RSA of 2048 bits or more, or
EC P-256/P-384, as PEM); from then on its secret no longer works. The client signs a short
JWT with its private key, which never leaves its systems, and sends it instead of a secret:

```
POST /api/v1/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
&client_assertion=<JWT>
```

The JWT is signed with RS256/384/512, PS256/384/512 or ES256/384 and carries `iss` and `sub`
(the client id), `aud` (the token endpoint's URL: `OPENCLEAR_PUBLIC_BASE_URL` +
`/api/v1/oauth/token`, or the address the request arrived at), `iat`, `exp` (at most five
minutes after `iat`) and a unique `jti`, which is accepted once. Anything else is
`401 invalid_client`. **Use a secret instead** goes back, with a new secret shown once.

The response is
`{"access_token": "...", "token_type": "Bearer", "expires_in": 1800}`
(`OPENCLEAR_API_CLIENT_TOKEN_MINUTES`, default 30); request a new one when it expires.
Errors follow RFC 6749: `401 {"error": "invalid_client"}` for an unknown client, a wrong
secret or a revoked client, and `400 {"error": "unsupported_grant_type"}`. The endpoint is
rate-limited per address (`OPENCLEAR_OAUTH_TOKEN_RATE_LIMIT_PER_MINUTE`, 30).

**Per-account access.** A customer's person (or a customer's API client) sees all of the
customer's accounts until the bank limits them to some: Users → Edit → **Choose accounts**,
or `PUT /api/v1/users/{membership_id}/accounts` with `{"all_accounts": false, "account_ids":
[...]}` (`GET` the same path to read it; `GET /api/v1/users` shows each membership's
`all_accounts` and `account_ids`). Limited, every endpoint returns only those accounts and
what's on them, an item on another account is `404`, and their bulk uploads can add items
only to those accounts.

**Customer self-service.** `GET`/`PUT /api/v1/customers/{customer_number}/self-service`
with `{"manages_people": true, "manages_sign_on": false, "manages_api_clients": false}` lets a
customer's own administrators manage its people, sign-on and API clients (or stops them at
once); `manages_api_clients` left out of a `PUT` stays as it is. The answer also lists its
`administrators`. The customer upsert (`PUT /api/v1/customers/{customer_number}`) never
changes these, so a core's sync leaves them alone; customer lists show them as
`self_manages_users`, `self_manages_sso` and `self_manages_api_clients`.

A client acts through its own service identity, so the token works on every endpoint its
security group allows, limited to its customer if it has one, and everything it does is in
the audit log under its name. Revoking it takes effect on its next request; rotating its
secret stops the old secret from getting new tokens. Nobody can sign in as a client's
service identity, by password or SSO.

**Acting for a user.** An online banking provider's server can act for the person signed
in to it, so a decision or recommendation is theirs and dual control still sees who made
it. The bank turns this on per client ("may act for your users"). The client then sends:

```
Authorization: Bearer <the client's token>
OpenClear-Act-As: pat@acme.example.com
OpenClear-Act-As-Customer: ACME     # only if that person belongs to several customers
```

- The person must already be an active member of the bank (and of the client's customer,
  if the client has one).
- The request gets only the permissions both the client and the person hold.
- The action's audit entry names the person, and every change made this way also records
  an `api_client.act_as` entry naming the client.
- Without the bank's permission, or for anyone else, the answer is `403`.
- `OpenClear-Act-As-Subject: <id>` names the person by the online banking provider's own id
  for them (its OIDC `sub`) instead of their email, for a client linked to that provider's
  sign-on connection. The person must have signed in, or been exchanged (below), at least once.

**Token exchange (RFC 8693).** An online banking provider whose people sign in with it
(the bank sets up its OIDC connection with a *customer claim*, so each person belongs to a
business customer) can trade the person's ID token for an OpenClear token that acts for
them. This also creates the person on first use, with the connection's group mappings:

```
POST /api/v1/oauth/token
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
client_id=occ_...&client_secret=...
subject_token=<the person's ID token from the provider>
subject_token_type=urn:ietf:params:oauth:token-type:id_token
```

The answer is `{"access_token", "issued_token_type": "urn:ietf:params:oauth:token-type:access_token", "token_type": "Bearer", "expires_in"}`.

- The token expires no later than the ID token does.
- What it allows is re-checked on every request, as with `OpenClear-Act-As`.
- The client must be allowed to act for users and be linked to the provider's connection
  (Admin → API clients).
- A refused exchange answers `400` with an RFC 6749 error:
  - `invalid_grant`: a bad or expired ID token, or a person with no mapped group or
    customer;
  - `unauthorized_client`: a client that isn't set up for exchange;
  - `invalid_request`: a malformed request.

| method | path | permission | notes |
|---|---|---|---|
| GET | `/api/v1/api-clients` | `api_client:manage`, or a customer's `customer_api_client:manage` | the API clients, for access reviews: name, client id, security group, customer, `managed_by` (`customer` for one a customer made for its own systems), `accounts`, created by, last used, rotated, revoked. Never a secret. A customer's own session sees only the clients it made |

### Using the access token

Every other endpoint requires:

```
Authorization: Bearer <access_token>
```

A token encodes `tenant_id`, `security_group_id`, and (if the membership is
customer-scoped) `customer_id`. Permissions are **not** baked into the token — they're
re-resolved from the security group's current permission set on every request, so
editing a group or deactivating a user/membership takes effect immediately, without
waiting for the token to expire. See [Permissions](#permissions) below.

### WebAuthn / FIDO2 (second factor)

All under `/api/v1/auth/webauthn`, and (except the two `/login/*` endpoints) require a
normal access token:

| method | path | auth | purpose |
|---|---|---|---|
| POST | `/register/options` | access token | begin registering a new credential for the current user |
| POST | `/register/verify` | access token | complete registration; body: `{"credential": {...}, "nickname": "..."}` (raw `navigator.credentials.create()` response) → `201` + `WebauthnCredentialRead` |
| GET | `/credentials` | access token | list the current user's registered credentials |
| DELETE | `/credentials/{credential_id}` | access token | remove a credential → `204` |
| POST | `/login/options` | `mfa_token` | begin the second factor after a password check that returned `mfa_required: true` |
| POST | `/login/verify` | `mfa_token` | complete it; body: `{"credential": {...}}` (raw `navigator.credentials.get()` response) → `TokenResponse` with real access/refresh tokens |

`WebauthnCredentialRead`: `id`, `nickname`, `aaguid`, `created_at`, `last_used_at`.

## Webhooks

OpenClear can tell your systems the moment something happens, instead of their polling.
Set endpoints up under Admin → Webhooks (`api_client:manage`): a URL, the events it
wants, and a signing secret (`whsec_...`, shown once).

| Event | When | `data` |
|---|---|---|
| `exception.created` | a presented check or ACH debit raised an exception | `exception_id`, `network`, `customer_id` |
| `exception.decided` | an exception was decided, by a person or automatically | `exception_id`, `decision_id`, `sequence` (its place in the [decision feed](#decision-feed--apiv1decisions)), `outcome` |
| `bulk_upload.completed` | a bulk upload finished, or failed | `upload_id`, `status`, `succeeded`, `failed` |
| `webhook.test` | the "Send a test event" button | `endpoint_id` |

Each message is a JSON `POST` in the [Standard Webhooks](https://www.standardwebhooks.com/)
format:

```json
{"type": "exception.decided", "timestamp": "2026-10-09T14:03:11+00:00",
 "data": {"exception_id": "...", "decision_id": "...", "sequence": 1042, "outcome": "return"}}
```

Payloads carry ids, not data: fetch the details through the API with your own
credentials, so a misconfigured endpoint can't leak anything. Headers:

- `webhook-id`: the message id, the same on every retry. Use it to ignore duplicates.
- `webhook-timestamp`: Unix seconds. Reject messages more than a few minutes old.
- `webhook-signature`: `v1,<signature>`, a base64 HMAC-SHA256, keyed with the secret
  (base64-decoded, after the `whsec_` prefix), of `{webhook-id}.{webhook-timestamp}.{body}`.

```python
import base64, hashlib, hmac

def verify(secret: str, headers: dict, body: bytes) -> bool:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s) for s in headers["webhook-signature"].split())
```

Any 2xx response within 10 seconds (`OPENCLEAR_WEBHOOK_TIMEOUT_SECONDS`) counts as
delivered. Anything else is retried after 5 s, 5 min, 30 min, 2 h, 5 h, 10 h and 10 h,
then marked failed. The endpoint's page lists recent deliveries, and can send a failed
one again. An event is written in the same transaction as what caused it, so it's sent
only for a change that was saved, and isn't lost if a server stops before sending it.

The URL must be public `https`, and it's checked again when connecting, so a name that
resolves to a private address is refused and redirects aren't followed. A single-bank
install whose receivers are on its own network can turn on
`OPENCLEAR_WEBHOOK_ALLOW_PRIVATE_HOSTS`. Leave it off when several banks share an
installation.

## Retrying writes safely: Idempotency-Key

Send `Idempotency-Key: <a unique value of your own>` (up to 255 characters, e.g. a UUID)
on any POST, PUT, PATCH or DELETE, and a retry after a timeout or dropped connection
can't do the work twice:

- the first request runs, and its response is kept against the key for 24 hours
  (`OPENCLEAR_IDEMPOTENCY_KEY_HOURS`);
- a retry with the same key and the same request gets that response again, with the header
  `Idempotency-Replayed: true`. Error responses (4xx) are replayed too;
- the same key with a different method, path, query or body: `422`;
- a retry while the first is still running: `409`, so try again shortly;
- a server error (5xx) isn't kept, so a retry runs again.

Keys belong to the caller (the person, or the API client) within its bank, so two
callers can't collide. Requests without the header behave as before.

## Paging list endpoints

`GET /exceptions`, `/paid-items`, `/issued-items`, `/stop-payments`, `/ach-transactions`
and `/ach-authorizations` return one page of rows, oldest first:

| query parameter | default | notes |
|---|---|---|
| `limit` | 500 | rows per page, at most 1,000 (more is a `422`) |
| `offset` | 0 | rows to skip |

The body is still a plain JSON list. Two response headers describe the rest:

- `X-Total-Count`: how many rows match your filters in total.
- `Link: <...>; rel="next"`: the URL of the next page, with your filters kept. It's absent
  on the last page.

To read everything, follow `Link` until it's gone. **Changed in 1.6.0:**
these endpoints used to return every row in one response. A client that expects that
still works for up to 500 rows; beyond that it must follow `Link`.

## Permissions

Every endpoint below is gated on one permission key from this catalog, checked against
the caller's current security group. There is no per-user permission override — a
membership always has exactly one security group governing what it can do, plus an
optional customer scope governing whose data it can see (see
[Multi-tenancy and customer scoping](#multi-tenancy-and-customer-scoping)).

| permission | grants |
|---|---|
| `account:read` / `account:write` | view / create accounts |
| `issued_item:read` / `issued_item:write` | view / create & void issued items |
| `stop_payment:read` / `stop_payment:write` | view / create & cancel stop payments |
| `paid_item:read` / `paid_item:write` | view / submit paid items |
| `check_image:read` / `check_image:write` | view / upload & reprocess check images |
| `ach_authorization:read` / `ach_authorization:write` | view / create & revoke ACH authorizations |
| `ach_transaction:read` / `ach_transaction:write` | view / submit & bulk-load ACH transactions |
| `exception:read` | view exceptions |
| `exception:recommend` | submit a maker recommendation (pay/return) |
| `exception:decide` | finalize a pay/return decision (checker) |
| `admin:manage` | manage ML models and payment networks |
| `user:manage` | manage users (list, add, edit entitlements, deactivate) |
| `security_group:manage` | manage security groups |
| `tenant:manage` | manage organization branding/settings |
| `audit_log:read` | view the immutable action log |
| `customer:manage` | manage customers |
| `api_client:manage` | create, rotate and revoke API clients for the bank's systems (bank-wide only) |
| `billing:read` | view billing statements and download the billing exports (a customer-scoped user sees only its own statement) |
| `billing:manage` | manage the fee schedule, billing settings and customer billing terms, and finalize a month |

A missing permission returns `403 Forbidden` with `{"detail": "Missing permission: <key>"}`.

An expired token returns `401` with `"Token expired"`; any other invalid/malformed token,
or a token whose user/membership/security group has since been deactivated or deleted,
returns `401` with `"Invalid token"` — these are indistinguishable from the outside, by
design, so a token can't be used to probe why access was revoked.

## Multi-tenancy and customer scoping

A membership is either **bank-wide** (`customer_id` is `null` on the token — sees every
customer's data in the tenant) or **scoped to one customer** (`customer_id` set — every
list/get endpoint below transparently filters to that customer only, and creates are
rejected with `404` if they'd reference a different customer's account).

Customer-scoped tokens additionally have a fixed set of bank-staff permissions masked out
regardless of what their security group nominally grants: `user:manage`,
`security_group:manage`, `tenant:manage`, `customer:manage`, `admin:manage`,
`audit_log:read`. These are always bank-wide-only concerns.

## Resource endpoints

Every write endpoint (`POST`/`PATCH`) records an entry in the tenant's tamper-evident
audit log (`channel="api"`) — this is automatic and not a separate call you need to make.

### Issued items — `/api/v1/issued-items`

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `issued_item:write` | body: `IssuedItemCreate` → `201` + `IssuedItemRead` |
| POST | `/bulk` | `issued_item:write` | body: JSON array of `IssuedItemCreate` → `BulkSubmitResponse` (see [Bulk uploads](#bulk-uploads)) |
| GET | `` | `issued_item:read` | query: `status_filter`, `account_id` |
| GET | `/outstanding` | `issued_item:read` | shortcut for `status_filter=outstanding` |
| GET | `/{item_id}` | `issued_item:read` | |
| PATCH | `/{item_id}/void` | `issued_item:write` | body: `{"reason": "..."}` |

`IssuedItemCreate`: `account_id`, `check_number`, `amount`, `payee_name`, `issue_date`.

`IssuedItemRead` adds: `id`, `status` (`outstanding` / `paid` / `voided` / `stopped` /
`stale`), `void_reason`, `created_at`.

### Stop payments — `/api/v1/stop-payments`

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `stop_payment:write` | body: `StopPaymentCreate` → `201` + `StopPaymentRead` |
| GET | `` | `stop_payment:read` | query: `status_filter` |
| GET | `/outstanding` | `stop_payment:read` | shortcut for `status_filter=active` |
| PATCH | `/{stop_id}/cancel` | `stop_payment:write` | |

`StopPaymentCreate`: `account_id`, `check_number`, `amount` (optional), `effective_date`,
`expiration_date` (optional), `reason` (optional).

`StopPaymentRead` adds: `id`, `status` (`active` / `expired` / `cancelled`), `created_at`.

No bulk endpoint exists for stop payments.

### Paid items — `/api/v1/paid-items`

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `paid_item:write` | body: `PaidItemCreate` → `201` + `PaidItemRead` |
| POST | `/bulk` | `paid_item:write` | body: JSON array of `PaidItemCreate` → `BulkSubmitResponse` |
| GET | `` | `paid_item:read` | query: `account_id` |
| GET | `/{item_id}` | `paid_item:read` | |

`PaidItemCreate`: `account_id`, `check_number`, `presented_amount`, `presented_date`.

`PaidItemRead` adds: `id`, `matched_issued_item_id`, `match_status` (`pending` /
`matched` / `exception`), `settlement_status` (`pending` / `paid` / `returned`),
`created_at`. Submitting a paid item automatically attempts to match it against an
outstanding issued item; a non-match becomes an exception item (see
[Exceptions and decisions](#exceptions-and-decisions--apiv1exceptions)).

### Check images — `/api/v1/check-images`

The one multipart/file endpoint in the API.

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `check_image:write` | multipart form: `front_image` (required file), `back_image` (optional file), `paid_item_id` (optional query/form param) → `201` + `CheckImageRead` |
| GET | `/{check_image_id}` | `check_image:read` | |
| POST | `/{check_image_id}/reprocess` | `check_image:write` | resets `ocr_status` to `pending` and re-queues OCR |

`CheckImageRead`: `id`, `paid_item_id`, `ocr_status` (`pending` / `completed` /
`failed`), `ocr_extracted_amount`, `ocr_extracted_payee`, `ocr_confidence`,
`ocr_provider`, `created_at`, `processed_at`. OCR runs as a background job after the
request returns — poll `GET /{check_image_id}` until `ocr_status` is no longer `pending`.

### ACH authorizations — `/api/v1/ach/authorizations`

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `ach_authorization:write` | body: `AchAuthorizationCreate` → `201` + `AchAuthorizationRead` |
| GET | `` | `ach_authorization:read` | query: `status_filter` |
| PATCH | `/{rule_id}/revoke` | `ach_authorization:write` | |

`AchAuthorizationCreate`: `account_id`, `originator_id`, `originator_name`, `receiver_id`
(optional — `null` means a blanket authorization for any receiver from this originator),
`max_amount` (optional), `frequency_limit` (optional), `allowed_sec_codes` (optional list
of strings), `effective_date`, `expiration_date` (optional).

`AchAuthorizationRead` adds: `id`, `status` (`active` / `revoked`), `created_at`.

No bulk endpoint exists for ACH authorizations.

### ACH transactions — `/api/v1/ach/transactions`

| method | path | permission | notes |
|---|---|---|---|
| POST | `` | `ach_transaction:write` | body: `AchTransactionCreate` → `201` + `AchTransactionRead` |
| POST | `/bulk` | `ach_transaction:write` | body: JSON array of `AchTransactionCreate` → `BulkSubmitResponse` |
| GET | `` | `ach_transaction:read` | query: `account_id` |
| GET | `/{transaction_id}` | `ach_transaction:read` | |

`AchTransactionCreate`: `account_id`, `originator_id`, `originator_name`, `receiver_id`
(optional), `amount`, `transaction_type` (`debit` / `credit`), `sec_code`,
`trace_number`, `effective_date`.

`AchTransactionRead` adds: `id`, `match_status` (`pending` / `matched` / `exception`),
`settlement_status` (`pending` / `paid` / `returned`), `created_at`. Matched against
active ACH authorizations the same way paid items are matched against issued items — a
non-match becomes an exception item.

### Exceptions and decisions — `/api/v1/exceptions`

Read-only inventory:

| method | path | permission | notes |
|---|---|---|---|
| GET | `` | `exception:read` | query: `network_code`, `status_filter` |
| GET | `/{exception_id}` | `exception:read` | |

`ExceptionRead`: `id`, `network_code`, `source_item_id`, `related_reference_id`,
`exception_types` (list of strings), `status` (`open` / `pending_approval` / `pay` /
`return` / `escalated` / `withdrawn`), `ml_score` (optional), `ml_model_version`
(optional), `decision_deadline` (optional), `created_at`.

Maker/checker decisioning:

| method | path | permission | notes |
|---|---|---|---|
| POST | `/{exception_id}/recommend` | `exception:recommend` | maker's first pass — body: `RecommendRequest` |
| POST | `/{exception_id}/decide` | `exception:decide` | checker's final call — body: `DecideRequest` |
| GET | `/{exception_id}/decision` | `exception:read` | fetch the recorded decision, `404` if none yet |

Both `RecommendRequest` and `DecideRequest`: `outcome` (`pay` / `return`),
`reason_code`, `notes` (optional).

Error responses specific to decisioning (all `HTTPException` with a matching status):

| condition | status |
|---|---|
| exception not found | `404` |
| exception already has a final decision | `409` |
| `/decide` called before a recommendation exists | `409` |
| the same user who recommended tries to also decide (maker/checker segregation) | `403` |

`DecisionRead`: `id`, `exception_item_id`, `outcome`, `reason_code`, `notes`,
`submitted_by_user_id` (the maker, if any), `decided_by_user_id` (the checker),
`decided_at`.

### Customers and accounts — `/api/v1/customers`, `/api/v1/accounts`

For a core or middleware keeping OpenClear in step, keyed by the bank's own numbers. A
`PUT` creates the record or updates it to match, so a nightly sync can send everything
without looking anything up first. It answers `201` when it created and `200` when it
updated. As with any `PUT`, fields left out are cleared. A bank whose core numbers
customers by CIF can use the CIF as the customer number.

| method | path | permission | notes |
|---|---|---|---|
| GET | `/customers` (`?external_customer_id=`) | `customer:manage` | paged |
| PUT | `/customers/{customer_number}` | `customer:manage` | `name` (required), `is_active`, `external_customer_id`, `tax_id`, contact and address fields, `notes` |
| GET | `/accounts` | `account:read` | paged; a customer-scoped caller sees its own |
| PUT | `/accounts/{account_number}` | `account:write` | `name` (required), `customer_number` (null for a house account), `ach_debit_block_mode`, `external_account_id` (the customer's own reference, matched in its files). A customer-scoped caller can only create or change its own customer's accounts |

Every create and update is audit-logged.

### Decision files — `/api/v1/decision-batches`

For a core that takes files rather than calling the feed. The bank turns on the outbox under
**Settings → Decision files**: a layout, how often, and the layout's settings. OpenClear then
writes each run's new decisions to a file and sends the `decision_batch.ready` webhook.

The built-in layouts:

- `openclear_csv`;
- `nacha_returns`, a NACHA return file of the ACH debits returned, with R codes from the
  bank's return reasons, checked against Moov's validator;
- any layout a plugin provides and the bank turned on.

These endpoints are for bank-wide callers only.

| method | path | notes |
|---|---|---|
| GET | `?unacknowledged_only=true` | the files, oldest first (`from_sequence`, `to_sequence`, `decision_count`, `layout`, `filename`, `sha256`, `size`, `acknowledged_at`); paged |
| POST | `` | write a file now (`{"layout": ...}` optional); `null` if there's nothing new |
| GET | `/{id}`, `/{id}/file` | the batch, and its file as written (`X-Content-SHA256`) |
| POST | `/{id}/acknowledge` | the core processed it. Moves the feed's `outbox` consumer forward, as far as acknowledged files are contiguous. Repeating it is harmless |

`GET /api/v1/decisions/file?layout=nacha_returns` uses the same saved settings.

### Embedding — `/api/v1/embed`

| method | path | notes |
|---|---|---|
| POST | `/launch` | `{"parent_origin": "https://banking.example.com", "path": "/ui/exceptions"}`. Answers `201` with `{"url", "expires_in": 60}`: a one-time link that opens `path` in a frame on `parent_origin`, as the person the call acts for. Only for an API client acting for someone (an exchanged token, `OpenClear-Act-As` or `OpenClear-Act-As-Subject`): `403` otherwise. `422` if the origin isn't on the bank's list (Settings → Embedding) or the path isn't a `/ui/` page. See [docs/guides/embedding.md](docs/guides/embedding.md) |

### Decision feed — `/api/v1/decisions`

Every pay or return decision, in order, with what a core needs to pay or return the item,
so it can act before the deadline without reading exceptions one by one. Each live
decision gets the bank's next `sequence` (1, 2, 3...) when it's made, human or automatic.
Numbers become visible in order, so reading "everything after N" never skips one. Fraud
training examples aren't items and aren't in the feed. Needs `exception:read`; a
customer-scoped caller sees only its customer's decisions.

| method | path | notes |
|---|---|---|
| GET | `?after=N&limit=500` | decisions with `sequence > after`, oldest first (`limit` up to 1,000). `X-Latest-Sequence` gives the newest; a full page has `Link: rel="next"`. Keep the last sequence you processed and pass it as `after` |
| GET | `/feed.csv?after=N` | the same as a CSV file (up to 10,000 rows), for a core that takes files |
| GET | `/file?layout=openclear_csv&after=N` | the same in a decision file layout: `openclear_csv`, or one a plugin provides that the bank turned on ([docs/PLUGINS.md](docs/PLUGINS.md)). `X-Last-Sequence` is the last decision in the file |
| POST | `/acknowledge` | `{"consumer": "core", "through_sequence": N}`: confirm a consumer processed through N. Only moves forward, never past the newest; bank-wide callers only; audit-logged |
| GET | `/consumers` | each consumer's acknowledged position and how many decisions it's `behind` |

Each decision: `sequence`, `decision_id`, `exception_id`, `network` (`check` / `ach`),
`outcome` (`pay` / `return`), `reason_code`, `return_transaction_code`, `source`
(`human`, `auto_default`, `auto_ml`), `decided_by` (email, or null for automatic),
`decided_at`, `account_number`, `customer_number`. Plus, for a check: `check_number`,
`amount`, `presented_date`; for an ACH debit: `trace_number`, `amount`, `effective_date`,
`originator_id`, `originator_name`, `sec_code`.

A typical core job, every few minutes: read `?after=<last>`, pay or return each item,
then acknowledge the last sequence it handled.

### Admin — `/api/v1/admin`

All gated on `admin:manage`. Each organization chooses its fraud-scoring model (Settings →
Fraud scoring model in the web UI): the **shared model**, trained on every participating
bank's decisions and run only by the platform operator (see "Platform — shared model"
below), or a **bank-only model** trained on its own decisions, which these endpoints manage.

| method | path | notes |
|---|---|---|
| GET | `/payment-networks` | list registered network codes (`list[str]`) |
| POST | `/ml/retrain?network_code=...` | retrain this organization's **bank-only** model → `RetrainResponse`; `403` for an organization on the shared model; `409` if there isn't enough training data yet, or it was retrained moments ago. The new version is activated only if it scores at least as well as the active one on the same recent decisions and was trained on enough of the bank's own decisions (`ml_bank_model_min_decisions`, default 200); the reason is in `metrics_json.evaluation.reason` |
| GET | `/ml/models` | this organization's own model versions (bank-only and customer models); optional `?network_code=` filter |
| GET | `/ml/shared-model` | the shared model's active version per network, with counts only → `list[SharedModelSummaryRead]` (`network_code`, `version`, `activated_at`, `trained_from_decision_count`, `contributing_bank_count`) |
| PATCH | `/ml/models/{model_id}/activate` | activate one of this organization's bank-only models; `404` for any other model. With segregation of duties enforced, answers `202` with `{"status": "pending", "change_request_id"}`: a second admin approves it in Admin → Changes |

`MlModelRead`: `id`, `network_code`, `version`, `algorithm`,
`trained_from_decision_count`, `metrics_json`, `status` (`training` / `active` /
`retired` / `failed`), `activated_at`, `created_at`.

`RetrainResponse`: `network_code`, `promoted` (bool), `metrics` (`dict[str, float]`),
`model` (`MlModelRead`).

Retrain and activate are written to the organization's audit log.

### Model risk — `/api/v1/mrm`

Gated on `admin:manage`, for the bank's own staff (COMPLIANCE_PLAN.md C2;
[docs/compliance/sr-11-7.md](docs/compliance/sr-11-7.md)). Both tests are dry runs: the real
rules run, with the bank's settings and plugins, and everything is rolled back except the
report.

| method | path | notes |
|---|---|---|
| POST | `/replay` | multipart `file`: a historical X9.37 cash letter or NACHA file (up to 50 MB). A file over `long_request_sync_bytes` (1 MB) is replayed in the background: `202` with the run, `state` queued; poll `GET /runs/{id}` until `state` is done (or failed). Otherwise `201` with the finished run: `report` has `items`, `compared_with_history`, `match_rate` (today's rules flag an item exactly when they did then), `false_positive_rate` (flagged, but the bank paid it), `missed_return_rate` (not flagged, but returned), `unhandled_rate`, `exception_types`, and `variances` listing the items that differ. `422` for a file that can't be read |
| POST | `/anomaly-capture` | runs the synthetic anomaly-capture test now. `passed` only if every injected fraud vector was caught and the clean items weren't flagged; `report.probes` has each one |
| GET | `/runs` | the latest runs; `?kind=mrm_replay` or `anomaly_capture` |
| GET | `/runs/{run_id}` | one run |

### Controls — `/api/v1/controls`

| method | path | notes |
|---|---|---|
| GET | `/status` | `audit_log:read`. Each control's live status, `PASS` / `WARN` / `FAIL`, with `detail` and `evidence`, and `overall` (the worst). The controls are: secrets encrypted in the database; disk and database encryption (as the operator states it); the action log's integrity; segregation of duties; admins' second factor; the anomaly-capture test; malformed files refused; decisions and imports on time; integration secret age; production settings |

### Compliance inventories — `/api/v1/compliance`

Read-only (CONTROLS_PLAN.md CT1). See [docs/compliance/controls.md](docs/compliance/controls.md) and
[cuecs.md](docs/compliance/cuecs.md).

| method | path | notes |
|---|---|---|
| GET | `/controls` | `audit_log:read`, the bank's own staff. OpenClear's control inventory: each control (`OC-AC-05`) with its NIST SP 800-53 controls (id, title, OSCAL id), type, nature, the CUECs it relies on, and its checks. Each check is `live` (with this bank's `result` and `detail` now) or `planned`; a control's `status` is the worst of its live checks, or null when none is live yet |
| GET | `/cuecs?party=bank\|customer\|host` | Any signed-in caller (the inventory is published). The complementary user entity controls, each with the OpenClear controls that rely on it. A customer's own session sees only the customer's (`party=bank` or `host` is `403`) |

### Evidence export — `/api/v1/audit`

| method | path | notes |
|---|---|---|
| GET | `/export?start=YYYY-MM-DD&end=YYYY-MM-DD&format=json\|pdf` | `audit_log:read`, the bank's own staff. A signed evidence bundle for the period (UTC days, at most 400). It covers the system version; the rules, plugins and models in force with their digests; settings and security groups; whether the action log verifies; segregation-of-duties breaches; changes and approvals; every decision with who recommended and decided it; model-risk runs; and the control status. JSON is `{"bundle", "signature"}`, where the signature is ECDSA P-256 over the SHA-256 of the bundle as compact, key-sorted JSON, with the public key included. The PDF shows the same. Both carry `X-OpenClear-File-SHA256` and `X-OpenClear-File-Signature` (the file as sent). Each export is in the action log. A period longer than `long_request_sync_days` (32) is exported in the background: `202` with JSON `status_url`. See [docs/compliance](docs/compliance/README.md) |
| GET | `/export/runs/{run_id}` | A background export's `state` (queued, running, done, failed); `download_url` when done |
| GET | `/export/runs/{run_id}/file` | The finished export, with the same `X-OpenClear-*` headers. `409` until it's done |

### Security events and the action log — `/api/v1/security-events`, `/api/v1/audit-log`

`audit_log:read`, the bank's own staff (SIEM_PLAN.md S4). Both are feeds read oldest first
with a cursor, like the decision feed.

| method | path | notes |
|---|---|---|
| GET | `/security-events?after=<sequence>&limit=` | This bank's security events after `after` (0 for the start): sign-ins and their failures, lockouts, second factors, tokens, refusals, changes to who may do what. Each has `id`, `sequence`, `time`, `event` (`name`, `category`, `outcome`, `severity`, `reason`), `bank`, `actor`, `target`, `source` (`ip`, `user_agent`), `request` (`id`, `channel`, `edge_id`), `audit` (the action-log sequence, when it's also an entry) and `details`. Continue with `next_after`. Kept for 30 days by default |
| GET | `/audit-log?after_sequence=<n>&limit=` | This bank's action log after `after_sequence`, each entry with `prev_entry_hash`, `entry_hash` (an ECDSA P-256 / SHA-256 signature, hex DER) and `signed_text` (exactly what was signed); `signature.public_key` checks them. Continue with `next_after_sequence` |
| GET | `/platform/security-events?after=&limit=` | Platform key with the `operations` scope: every bank's events, and those belonging to no bank (e.g. a failed sign-in naming a bank that doesn't exist) |

Every response carries `X-Request-ID`. A signed-in caller is rate-limited as themselves
(`rate_limit_per_principal_per_minute`); anyone else by address. A body over the size limit
gets `413` with a JSON `detail` under `/api`.

### Billing — `/api/v1/billing`

What a bank charges its customers for positive pay. Each customer is billed either through
**account analysis** (charges go to the bank's analysis system, which offsets them with
earnings credits; ISO 20022 payment method `BCMP`) or as a **hard charge** (a direct debit
to the customer's billing account; `FLAT`), or is `exempt`. The bank sets a default and can
override it per customer. OpenClear counts volumes and prices them; it doesn't calculate
earnings credits.

A fee item charges a **metric** (`GET /metrics`) at a unit price, optionally with step
tiers and a monthly minimum, and can carry the bank's AFP Service Code. Metrics count per
account, per customer or bank-wide:

| metric | counts | level |
|---|---|---|
| `customer_monthly` | customers enrolled at month end | customer |
| `account_monthly` / `ach_account_monthly` | accounts enrolled (check / ACH positive pay) | account |
| `user_monthly` | users with access | customer |
| `ach_rule_monthly` | active ACH authorization rules | account |
| `customer_model_monthly` | customer-specific fraud models | customer |
| `issued_items` / `issue_files` | issued checks loaded / issue files received | account / customer |
| `presented_checks` / `ach_debits` | items evaluated | account |
| `check_exceptions` / `ach_exceptions` | exceptions raised | account |
| `check_returns` / `ach_returns` / `items_returned` | items returned | account |
| `items_paid_on_decision` / `auto_decisions` | exceptions decided pay / decided by the default | account |
| `stop_payments`, `check_images`, `wsud_statements`, `files_received`, `data_exports` | as named | account / customer |
| `sms_sent` / `email_sent` | notifications sent | bank |

Bank-level metrics appear on the bank-wide statement (`customer_id: null`), which uses the
bank's own billing method.

| method | path | permission | notes |
|---|---|---|---|
| GET | `/metrics` | `billing:read` | the metric catalog → `MetricRead` (`code`, `label`, `unit`, `level`) |
| GET | `/fee-items` | `billing:manage` | the fee schedule → `FeeItemRead` list |
| POST | `/fee-items` | `billing:manage` | add a fee (`FeeItemCreate`); `422` for an unknown metric, a duplicate code, or bad tiers |
| POST | `/fee-items/starter-schedule` | `billing:manage` | add the common positive pay fees the bank doesn't have yet, priced at zero |
| PATCH | `/fee-items/{id}` | `billing:manage` | change a fee |
| GET / PUT | `/settings` | `billing:manage` | `customer_default_billing_method`, `bank_billing_method`, `bank_billing_account_id` |
| GET / PUT | `/customers/{id}/terms` | `billing:manage` | a customer's billing method, billing account, discount %, waive-all, notes and per-fee `overrides` (`unit_price` or `waived`); `is_default` when it has no terms of its own |
| GET | `/statements` | `billing:read` | statements for `?period=2026-09` (or `start` and `end`), optional `customer_id`; live by default, the finalized version with `finalized=true` |
| GET | `/report.csv` | `billing:read` | the billing report, one row per statement line; same filters |
| GET | `/periods/{period}` | `billing:manage` | whether the month is finalized, and its versions |
| POST | `/periods/{period}/finalize` | `billing:manage` | freeze the month's statements; finalizing again saves a replacement version |
| GET | `/periods/{period}/analysis-feed.csv` | `billing:read` | analysis customers' service lines for the analysis system |
| GET | `/periods/{period}/hard-charge-postings.csv` | `billing:read` | one debit per hard-charge customer, for the core's fee posting |
| GET | `/periods/{period}/camt086.xml` | `billing:read` | the month as an ISO 20022 camt.086 Bank Services Billing message (optional `customer_id`) |

The three exports are made from the finalized month only (`409` until it's finalized) and
are for bank staff (`403` for a customer-scoped user). camt.086 marks the first version
`ORGN` and later ones `RPLC`; the namespace version is `OPENCLEAR_BILLING_CAMT086_VERSION`
(default `camt.086.001.04`). Validate it against your counterparty's schema before using it
in production. Finalizing is written to the audit log.

A statement: `period_start`, `period_end`, `customer_id`, `customer_number`,
`customer_name`, `billing_method`, `billing_account_number`, `totals` (`list`, `discount`,
`waived`, `net`) and `lines`. Each line: `fee_code`, `description`, `metric`, `unit`,
`afp_code`, `bank_service_code`, `volume`, `unit_price`, `charge_method` (camt.086 `UPRC`,
`TIR1`…, `MCHG`), `list_amount`, `discount_amount`, `net_amount`, `payment_method`
(`BCMP`, `FLAT`, `WVED`, `FREE`), `tiers`, and `accounts` (volume per account). Amounts are
strings.

### Platform — shared model — `/api/v1/platform`

For the platform operator only, **not** a user token: send a platform API key with the
`shared_model` scope as `X-Api-Key` (mint one with
`python scripts/create_metering_api_key.py "Platform ML operator" --scope shared_model`).
`401` for a bad key, `403` for a key without the scope. A usage-metering key can't use these,
and a `shared_model` key can't read usage.

| method | path | notes |
|---|---|---|
| GET | `/ml/models` | the shared model's versions; optional `?network_code=` |
| POST | `/ml/retrain?network_code=...` | retrain the shared model (decisions from banks on the shared model only, plus approved fraud-training examples) → `RetrainResponse` |
| PATCH | `/ml/models/{model_id}/activate` | activate a shared-model version; `404` for a bank's own model |
| GET | `/ml/models/{model_id}/feature-importance` | the model's coefficients by feature, largest effect first |
| GET | `/ml/fraud-examples/pending` | fraud-training examples from banks on the shared model that aren't yet approved to train it → `list[PendingFraudExampleRead]` |
| POST | `/ml/fraud-examples/{exception_id}/approve` | approve one for the shared model (`204`); recorded in that bank's audit log |
| POST | `/tenants/{tenant_id}/ml-switch-lock/clear` | let a new organization switch to a bank-only model before its first 90 days are up; recorded in its audit log |

When an organization switches to a bank-only model, the next scheduled retrain rebuilds the
shared model without its data and activates the result even if it scores lower, because
removing that bank's data comes first.

### Platform — background jobs — `/api/v1/platform/jobs`

For the platform operator: a platform API key with the `operations` scope, as `X-Api-Key`
(`python scripts/create_metering_api_key.py "Operations" --scope operations`).

| method | path | purpose |
|---|---|---|
| GET | `/summary` | count of jobs by status: `{"queued": 3, "running": 1, "succeeded": 120, "failed": 0}` |
| GET | `` (`?status_filter=failed&kind=...`) | jobs, newest first, paged (see [Paging](#paging-list-endpoints)) → `JobRead` list |
| POST | `/{job_id}/retry` | queue a `failed` job again with a fresh set of attempts; `409` for any other status |

`JobRead`: `id`, `kind` (`check_image.ocr`, `data_export.run`, `dropbox.scan_bank`,
`ml.retrain_slot`), `tenant_id`, `status` (`queued` / `running` / `succeeded` / `failed`),
`attempts`, `max_attempts`, `run_after`, `created_at`, `started_at`, `finished_at`,
`last_error`. A job reaches `failed` only after `max_attempts` tries; watch
`/summary` (or `?status_filter=failed`) and retry once the cause is fixed.

### Platform — billing banks — `/api/v1/platform/billing`

For the platform operator to bill each bank: a platform API key with the `billing` scope
(`python scripts/create_metering_api_key.py "Billing" --scope billing`). The operator keeps
its own fee schedule, using the same metrics as the banks plus `bank_monthly` (one per
bank). Each bank's invoice counts its whole organization (every customer and the bank's
own activity), is always a hard charge (`FLAT`), and leaves out the public demo.

| method | path | notes |
|---|---|---|
| GET | `/metrics` | the metric catalog, with `bank_monthly` |
| GET / POST | `/fee-items` | the operator's fee schedule (same fields as a bank's) |
| PATCH | `/fee-items/{id}` | change a fee |
| GET | `/invoices` | each bank's invoice for `?period=2026-09` (or `start` and `end`), optional `tenant_id`; the statement shape above, with `bank_slug` and `bank_name` in place of `customer_number` and `customer_name` |
| GET | `/invoices.csv` | the same as a report |
| GET | `/invoices/camt086.xml` | `?period=` (optional `tenant_id`) as camt.086, sent by `OPENCLEAR_PLATFORM_OPERATOR_NAME` / `_ID` |

Invoices are calculated live. There's no finalized platform invoice yet.

### Users — `/api/v1/users`

| method | path | permission | notes |
|---|---|---|---|
| GET | `` | `user:manage` | one row per membership — see below |

This is a read-only access-review / recertification endpoint: it runs the exact same
query as the `/ui/users` web page, so the two can never drift apart. There is currently
no API for creating, editing, or deactivating users/memberships — those actions are
web-UI-only (`/ui/users/*`).

`TenantUserRead`: `user_id`, `email`, `security_group_name`, `customer_name` (`null` =
bank-wide), `is_active` (the *membership's* status, not the underlying login's — the same
login can be active in one tenant and deactivated in another), `membership_id`,
`membership_created_at`, `last_login_at` (`null` if this identity has never completed a
login).

## Bulk uploads

`POST .../bulk` exists for **issued items, ACH transactions, and paid items** — each
takes a plain JSON array of the same `Create` schema used by the single-item `POST`,
and returns:

```json
{
  "total": 3,
  "succeeded": 2,
  "failed": 1,
  "results": [
    {"index": 0, "success": true, "id": "...", "status": null, "error": null},
    {"index": 1, "success": true, "id": "...", "status": "matched", "error": null},
    {"index": 2, "success": false, "id": null, "status": null, "error": "Account not found"}
  ]
}
```

Rows are processed independently — one bad row doesn't fail the whole batch, and
`results[i].index` maps each result back to its position in the request array.

**Large requests are imported in the background.** Up to `OPENCLEAR_BULK_API_SYNC_MAX_ROWS`
items (1,000 by default) are imported during the call and answered as above. A larger
array is stored and imported by a background job, and the call answers **`202 Accepted`**:

```json
{"upload_id": "...", "status": "queued", "total": 25000, "status_url": "/api/v1/bulk-uploads/..."}
```

Poll the upload until it's finished, then read the row results:

| method | path | notes |
|---|---|---|
| GET | `/api/v1/bulk-uploads/{id}` | `status` (`queued`, `processing`, `completed`, `rejected`, `failed`), `processed_rows` of `total_rows`, `succeeded`, `failed`, `error`, `source` (`api`, `manual`, `auto_import`) |
| GET | `/api/v1/bulk-uploads/{id}/results` | each row's `index`, `label`, `success`, created `id` and `error`, in order; paged (see [Paging](#paging-list-endpoints)); `?failures_only=true` |

Both need the permission that gated the upload (`paid_item:write`, `issued_item:write`,
`ach_transaction:write`, ...); web and dropbox uploads can be read here too. Rows already
committed stay if an import `failed`. New exceptions from a background import send one
summary notification per reviewer rather than one each.

**Uploading a file.** You can also send a whole file in any import format the bank can use: the built-in ones, and any a plugin provides that a bank admin turned on ([docs/PLUGINS.md](docs/PLUGINS.md)). It's imported in the background like the large requests above.

| method | path | notes |
|---|---|---|
| GET | `/api/v1/bulk-uploads/formats` | the `kind` and `format` pairs the caller may upload, each with `built_in` |
| POST | `/api/v1/bulk-uploads` | `multipart/form-data`: `kind` (e.g. `paid_items`), `format` (e.g. `tabular`, `nacha`, `x937`), `file`, and optionally `create_missing_accounts`. Answers `202` with `status_url`. A format that doesn't exist or isn't turned on gets `422`. A file that can't be read comes back `rejected` |

Needs the permission for that `kind`.

There is no `/bulk` endpoint for stop payments, ACH authorizations, or check images.
