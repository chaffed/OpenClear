# OpenClear SDK for Python

This is a typed Python client for the [OpenClear](https://github.com/chaffed/OpenClear) API. It's for banking cores, online banking providers and a bank's own systems that send items to OpenClear and act on its pay and return decisions.

It's licensed Apache-2.0, so you can ship it inside proprietary software. OpenClear itself is AGPL-3.0.

```sh
pip install openclear-sdk
```

It requires Python 3.11 or later and has one dependency, `httpx`.

## Connecting

To connect, you need an API client. A bank admin creates one under **Admin → API clients**, chooses a security group (what the client may do) and, optionally, a customer (whose data it may see). The admin then gives you the client id and secret. The secret is shown only once.

```python
from openclear_sdk import OpenClear

oc = OpenClear("https://openclear.yourbank.example", client_id="occ_...", client_secret="...")
```

The client fetches the access token itself and renews it before it expires.

Other ways to authenticate:

- To use a token you already have, pass `access_token=...`.
- For the platform endpoints (`platform_*`), pass `platform_api_key=...`.

## Calling the API

Each API area is an attribute of the client, with one method per endpoint. The methods are generated from OpenClear's published OpenAPI contract, and their arguments and results are typed with `TypedDict`.

```python
oc.customers.upsert_customer("CIF-1001", {"name": "Acme Corp"})
oc.accounts.upsert_account("12345678", {"name": "Operating", "customer_number": "CIF-1001"})
item = oc.issued_items.create_issued_item({
    "account_id": account_id, "check_number": "1001", "amount": "250.00",
    "payee_name": "Jane Doe", "issue_date": "2026-10-01",
})
```

**Lists.** Every list endpoint has a `list_...` method, which returns one page, and an `iter_...` method, which follows the pages for you:

```python
for item in oc.issued_items.iter_issued_items(status_filter="issued"):
    ...
```

**Retries.**

- Every write carries an `Idempotency-Key`, and the same key is reused on every retry, so a retried request never acts twice.
- Connection failures and responses 429, 502, 503 and 504 are retried with backoff. Set `max_retries` (default 3) to change how many times.

**Errors.** They're typed and all inherit from `OpenClearError`:

| Error | When |
|---|---|
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `ValidationError` | 422 (`.errors` lists the field problems) |
| `RateLimitError` | 429 (`.retry_after`) |
| `ServerError` | 5xx |
| `APIConnectionError` | no response |

## Sending many items

```python
result = oc.bulk.submit("issued_items", items)   # also "paid_items", "ach_transactions"
print(result.succeeded, result.failed)
for failure in result.failures:
    print(failure["index"], failure["error"])
```

`submit` works like this:

- It splits the list into batches.
- If a batch is too large for OpenClear to import during the call, OpenClear answers `202` and imports it in the background. `submit` waits for that import to finish.
- It returns one result per item, in the order you sent them.

To send a file in one of your core's formats, use `upload_file`. It works for OpenClear's own formats and for formats a plugin adds, once the bank has turned them on:

```python
oc.bulk.upload_file("paid_items", "tabular", open("presented.csv", "rb"))
```

## Acting on decisions

The decision feed lists every pay and return decision in order. The usual core job reads the new decisions, acts on each one, and acknowledges how far it got:

```python
def handle(decision):
    if decision["outcome"] == "return":
        core.return_item(decision["account_number"], decision["check_number"] or decision["trace_number"])
    else:
        core.pay_item(...)

oc.decision_feed.consume("core", handle)   # run every few minutes
```

If `handle` raises an error, the decisions after the last acknowledged page are delivered again on the next run. Make `handle` safe to run twice for the same decision.

To get a file instead, use `oc.decision_feed.decision_file(layout="openclear_csv", after=n)`. It also returns layouts that a plugin adds.

## Webhooks

Verify each webhook before acting on it. Pass the raw request body:

```python
from openclear_sdk import webhooks, WebhookVerificationError

event = webhooks.verify(secret, request.headers, request.body)   # raises WebhookVerificationError
if event["type"] == "exception.created":
    exception = oc.exceptions.get_exception(event["data"]["exception_id"])
```

Webhook payloads only carry ids, so fetch the details through the API. A retried webhook keeps its `webhook-id`, so you can use that header to skip events you've already handled.

## Acting for a person

If the bank has allowed your client to act for its people, you can make calls on a person's behalf:

```python
as_jane = oc.acting_as("jane@acme.example", customer_number="CIF-1001")
as_jane.decisions.decide_exception(exception_id, {"outcome": "pay"})
```

The call can only do what both your client and that person are allowed to do. The bank's action log records the action under the person's name and notes that your client made it.

### If you're the bank's online banking platform

If the bank signs its business customers' people in through your platform (an OIDC sign-on connection with a customer claim), and links your API client to that connection, you have two options:

- **Exchange the person's ID token.** You get a client that acts for that person within their own customer. The person is created in OpenClear on first use.
- **Name the person by your own id for them.** This works once they've signed in or been exchanged at least once.

```python
jo = oc.for_person(id_token_from_your_sign_in)   # token exchange: acts for the signed-in person
jo.exceptions.list_exceptions()
oc.acting_as_subject("your-user-id-for-them")     # or name them by your own id for them
```

## Command line

The package installs an `openclear` command for scripts and cron jobs. It reads its credentials from `OPENCLEAR_URL`, `OPENCLEAR_CLIENT_ID` and `OPENCLEAR_CLIENT_SECRET`.

```sh
openclear push paid_items presented.csv                 # import a file, list the rows that failed
openclear decisions pull --consumer core --format csv   # new decisions, then acknowledge them
openclear decisions files --download ./outbox           # outbox files, SHA-256 checked, then acknowledged
openclear webhooks tail --secret whsec_...              # receive webhooks locally, verify and print them
openclear compliance attestations --start 2026-01-01 --end 2026-03-31 --format oscal  # a quarter's attestations
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done |
| 1 | Some rows failed, or a file didn't verify |
| 2 | Bad usage or credentials |

## Version

`openclear_sdk.API_VERSION` is the OpenClear API version the SDK was generated from. `/api/v1` only changes in ways that add to it, so newer servers keep working with this SDK.
