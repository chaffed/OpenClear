# OpenClear SDK for TypeScript and JavaScript

This is a typed client for the [OpenClear](https://github.com/chaffed/OpenClear) API. It's for banking cores, online banking providers and a bank's own systems that send items to OpenClear and act on its pay and return decisions.

It's licensed Apache-2.0, so you can ship it inside proprietary software. OpenClear itself is AGPL-3.0.

```sh
npm install @openclear/sdk
```

It has no runtime dependencies. It uses `fetch` and Web Crypto, so it runs on Node 20+, Deno, Bun and Cloudflare Workers.

## Connecting

To connect, you need an API client. A bank admin creates one under **Admin → API clients**, chooses a security group (what the client may do) and, optionally, a customer (whose data it may see). The admin then gives you the client id and secret. The secret is shown only once.

```ts
import { OpenClear } from "@openclear/sdk";

const oc = new OpenClear({ baseUrl: "https://openclear.yourbank.example", clientId: "occ_...", clientSecret: "..." });
```

The client fetches the access token itself and renews it before it expires.

Other ways to authenticate:

- To use a token you already have, pass `accessToken`.
- For the platform endpoints (`platform*`), pass `platformApiKey`.

## Calling the API

Each API area is a property of the client, with one method per endpoint. The methods are generated from OpenClear's published OpenAPI contract, and every model is exported as a type.

```ts
await oc.customers.upsertCustomer("CIF-1001", { name: "Acme Corp" });
await oc.accounts.upsertAccount("12345678", { name: "Operating", customer_number: "CIF-1001", custom_fields: { aggregation_id: "G-42" } });
const item = await oc.issuedItems.createIssuedItem({
  account_id: accountId, check_number: "1001", amount: "250.00", payee_name: "Jane Doe", issue_date: "2026-10-01",
});
```

**Lists.** Every list endpoint has a `list...` method, which returns one page, and an `iter...` method, which follows the pages for you:

```ts
for await (const item of oc.issuedItems.iterIssuedItems({ status_filter: "issued" })) { ... }
```

**Retries.**

- Every write carries an `Idempotency-Key`, and the same key is reused on every retry, so a retried request never acts twice.
- Network failures and responses 429, 502, 503 and 504 are retried with backoff. Set `maxRetries` (default 3) to change how many times.

**Errors.** They're typed and all extend `OpenClearError`:

| Error | When |
|---|---|
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `ValidationError` | 422 (`.errors` lists the field problems) |
| `RateLimitError` | 429 (`.retryAfter`) |
| `ServerError` | 5xx |
| `APIConnectionError` | no response |

### Custom fields

A bank's own fields on customers and accounts come and go as `custom_fields`, a map from the field's key to a **string**: an ISO date (`2026-10-01`), decimal text (`12.5`), `true`/`false`, or a choice's option value. `GET /api/v1/custom-fields` gives each field's type; convert on your side. In a `PUT`, keys you leave out keep their values, and `null` clears one; a required field is required in people's forms only, so a sync written before the field existed keeps working.

## Sending many items

```ts
const result = await oc.bulk.submit("issued_items", items); // also "paid_items", "ach_transactions"
for (const failure of result.failures) console.log(failure.index, failure.error);
```

`submit` works like this:

- It splits the list into batches.
- If a batch is too large for OpenClear to import during the call, OpenClear answers `202` and imports it in the background. `submit` waits for that import to finish.
- It returns one result per item, in the order you sent them.

To send a file in one of your core's formats, use `oc.bulk.uploadFile(kind, format, file)`. It works for OpenClear's own formats and for formats a plugin adds, once the bank has turned them on.

## Acting on decisions

The decision feed lists every pay and return decision in order. The usual core job reads the new decisions, acts on each one, and acknowledges how far it got:

```ts
await oc.decisionFeed.consume("core", async (decision) => {
  if (decision.outcome === "return") await core.returnItem(decision);
  else await core.payItem(decision);
}); // run every few minutes
```

If `handle` throws, the decisions after the last acknowledged page are delivered again on the next run. Make it safe to run twice for the same decision.

To get a file instead, use `oc.decisionFeed.decisionFile({ layout: "openclear_csv", after })`. It returns a `Uint8Array`, and also covers layouts a plugin adds.

## Webhooks

Verify each webhook before acting on it. Pass the raw request body:

```ts
import { webhooks } from "@openclear/sdk";

const event = await webhooks.verify(secret, request.headers, await request.text()); // throws WebhookVerificationError
if (event.type === "exception.created") {
  const exception = await oc.exceptions.getException(String(event.data.exception_id));
}
```

Webhook payloads only carry ids, so fetch the details through the API. A retried webhook keeps its `webhook-id`, so you can use that header to skip events you've already handled.

## Acting for a person

If the bank has allowed your client to act for its people, you can make calls on a person's behalf:

```ts
await oc.actingAs("jane@acme.example", { customerNumber: "CIF-1001" }).decisions.decideException(id, { outcome: "pay" });
```

The call can only do what both your client and that person are allowed to do. The bank's action log records the action under the person's name and notes that your client made it.

### If you're the bank's online banking platform

If the bank signs its business customers' people in through your platform (an OIDC sign-on connection with a customer claim), and links your API client to that connection, you have two options:

- **Exchange the person's ID token.** You get a client that acts for that person within their own customer. The person is created in OpenClear on first use.
- **Name the person by your own id for them.** This works once they've signed in or been exchanged at least once.

```ts
const jo = await oc.forPerson(idTokenFromYourSignIn); // token exchange: acts for the signed-in person
await jo.exceptions.listExceptions();
oc.actingAsSubject("your-user-id-for-them");           // or name them by your own id for them
```

## Developing

```sh
npm install
npm run typecheck
npm test
npm run build
```

`npm test` runs the unit tests directly with Node 22.18+, with no build step. To run the live tests, run `tests/test_sdk/test_typescript_sdk.py` from OpenClear's repo. It starts a server, then runs these tests against it.
