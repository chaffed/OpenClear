# Integration checklist (certification)

This is how a core, an online banking provider, or a bank's own systems show that their OpenClear integration does what banks rely on.

**Where to see it:** OpenClear reads the results from what the integration actually did in its bank, usually a free sandbox at `/ui/sandbox`. You see them under **Admin → Integration checklist** or at `GET /api/v1/conformance`. When every required check passes, the integration meets the checklist.

## Required

| Check | What passes it | Why banks need it |
|---|---|---|
| Authenticates with an API client | A request with an OAuth client-credentials token (`POST /api/v1/oauth/token`) | No shared passwords, and access can be revoked at once |
| Syncs customers and accounts | `PUT /api/v1/customers/{number}` or `PUT /api/v1/accounts/{number}` | Items match the right account, and customers see their own |
| Sends issued checks | `POST /api/v1/issued-items` or `/bulk`, or an issued-items file | That's positive pay: without the issued file, every check is an exception |
| Sends presented items | Paid items, check images (X9.37) or ACH debits | So they're matched before they post |
| Sends an Idempotency-Key with writes | Any write carrying the header in the last day (the SDKs do this) | A retry after a timeout must never create an item twice |
| Acts on decisions and acknowledges them | A decision-feed consumer acknowledged through the latest decision, or decision files acknowledged | Pay and return decisions are useless until the core acts on them before the deadline |
| Receives webhooks | A delivery that succeeded | Exceptions get reviewed while there's still time |
| Verifies webhook signatures | **The probe:** OpenClear sends a correctly signed event, which must be accepted (2xx), and a forged one, which must be rejected (4xx) | Without it, anyone could post fake events to the receiver |

## Optional

These show the parts an integration uses:

- **Acts for signed-in people:** token exchange or `OpenClear-Act-As`.
- **Embeds OpenClear's pages:** embedding launch links.
- **Takes decision files from the outbox.**

## Getting there in a sandbox

1. **Create a sandbox** at `/ui/sandbox`. You get a seeded bank, its admin sign-in, and an API client.
2. **Generate test files** with Moov, and optionally CheckSim's checks: `scripts/sandbox_test_files.py`. Load them with `--load`, or through your own integration.
3. **Decide a few exceptions** in the web UI, as the bank would.
4. **Run your decision job:** your feed consumer or file pickup.
5. **Register your webhook receiver** under Admin → Webhooks, and send a test event.
6. **Click "Probe my webhook receiver"** on the checklist, or `POST /api/v1/conformance/webhook-probe`.

The SDKs (Python, TypeScript, .NET) handle tokens, idempotency keys, paging, decision consumption and webhook verification. An integration built on one passes most checks by using it as documented.
