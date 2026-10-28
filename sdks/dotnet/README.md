# OpenClear SDK for .NET

This is a typed .NET 8 client for the [OpenClear](https://github.com/chaffed/OpenClear) API. It's for banking cores and the middleware around them, which are mostly .NET. It's licensed Apache-2.0 and has no dependencies beyond the .NET base library.

```csharp
using OpenClear;

using var oc = new OpenClearClient(new OpenClearOptions {
    BaseUrl = "https://openclear.yourbank.example", ClientId = "occ_...", ClientSecret = "...",
});

await oc.Accounts.UpsertAccountAsync("12345678", new AccountUpsert { Name = "Operating", CustomerNumber = "CIF-1001",
    CustomFields = new Dictionary<string, string?> { ["aggregation_id"] = "G-42" } });

await foreach (var item in oc.IssuedItems.IterIssuedItemsAsync(statusFilter: "issued")) { /* ... */ }

// The usual core job: act on each new decision, acknowledging as you go.
await oc.DecisionFeedHelper.ConsumeAsync("core", async decision => {
    if (decision.GetProperty("outcome").GetString() == "return") await core.ReturnAsync(decision);
});

// Decision files from the outbox.
foreach (var batch in await oc.DecisionBatches.ListDecisionBatchesAsync(unacknowledgedOnly: true)) {
    var file = await oc.DecisionBatches.GetDecisionBatchFileAsync(batch.Id);
    // ... hand it to the core, then:
    await oc.DecisionBatches.AcknowledgeDecisionBatchAsync(batch.Id);
}

// Webhooks: verify the raw body before trusting it.
var evt = Webhooks.Verify(secret, headers, rawBody);
```

**Generated code.** The models (records) and endpoint methods (`…Async`, plus `Iter…Async` for lists) are generated from OpenClear's OpenAPI contract by `scripts/generate_sdks.py`. Dates, ids and enum values are strings, so a value a newer server adds never fails to parse.

**Retries.**

- Every write carries an `Idempotency-Key`, and the same key is reused on every retry, so a retried request never acts twice.
- Connection failures and responses 429, 502, 503 and 504 are retried with backoff. `Retry-After` is honoured.

**Errors.** They're typed and all inherit from `OpenClearException`:

| Exception | When |
|---|---|
| `AuthenticationException` | 401 |
| `PermissionDeniedException` | 403 |
| `NotFoundException` | 404 |
| `ConflictException` | 409 |
| `ValidationException` | 422 |
| `RateLimitException` | 429 |
| `ServerException` | 5xx |
| `ApiConnectionException` | no response |

**Building and testing.** No local .NET install is needed:

```sh
docker run --rm -v "$PWD":/sdk -w /sdk/tests/OpenClear.Tests mcr.microsoft.com/dotnet/sdk:8.0 dotnet test
```

### Custom fields

A bank's own fields on customers and accounts come and go as `custom_fields`, a map from the field's key to a **string**: an ISO date (`2026-10-01`), decimal text (`12.5`), `true`/`false`, or a choice's option value. `GET /api/v1/custom-fields` gives each field's type; convert on your side. In a `PUT`, keys you leave out keep their values, and `null` clears one; a required field is required in people's forms only, so a sync written before the field existed keeps working.
