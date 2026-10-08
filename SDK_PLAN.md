# OpenClear integration SDK plan

Started 2026-10-09. Goal: make it cheap and safe for a **core banking provider** (or the
bank's own back office) and an **online banking provider (OBP)** to connect to OpenClear,
so an implementation is weeks of configuration, not months of custom work. Tracked like
the other plans: phases on their own branches, decisions below, a progress log at the
bottom. Nothing is pushed or published without asking.

## Who integrates, and what they need

| Integrator | Direction | What flows | Today in OpenClear |
|---|---|---|---|
| **Core / item processing** (Fiserv, Jack Henry, FIS, CSI, or the bank's middleware) | in | presented checks (CSV, X9.37), ACH debits (NACHA), accounts and customers | REST bulk endpoints, file uploads, a dropbox directory |
| | out | **pay/return decisions** before the return deadline, so the core returns the items | Only per exception (`GET /exceptions/{id}/decision`); no feed, no file |
| **Online banking provider** (Q2, Alkami, Banno, Narmi, ...) for business customers | in | issued checks, stop payments, ACH authorization rules, decisions by the customer's users | REST API, but only with a user's password login |
| | out | exceptions to review, deadlines, results | No events; polling only |
| | UI | either OpenClear's own pages inside the OBP, or the OBP's native screens on OpenClear's API | Pages can't be framed (`frame-ancestors 'none'`); no sign-on handoff for customer users |

How the OBPs take third parties (research, 2026-10-09):
- **Q2**: an SDK (Python) whose extensions Q2 hosts, plus a marketplace; positive pay is
  a native Q2 feature, so OpenClear would sit behind it or beside it.
- **Alkami**: five integration types: custom widget and microservice, **SDK provider**
  (a third party behind Alkami's own widgets, the natural fit for positive pay), standard
  SSO, SDK SSO, and API.
- **Banno (Jack Henry)**: the Plugin Framework. A plugin is a web app shown as a dashboard
  card in a **sandboxed iframe**, authenticated through an "external application" with
  redirect URIs (OAuth). Third-party cookies in iframes are unreliable (Safari), so Jack
  Henry advises hosting on a subdomain of the bank's Banno domain. Jack Henry also
  publishes its own Positive Pay consumer API (check and ACH decision exceptions, issued
  items, file formats, ACH filters): the shape an OBP already expects.
- **Cores**: Jack Henry's jXchange (SOAP, moving to REST) under its Vendor Integration
  Program, which needs an agreement, certification and a sponsoring bank. Most cores move
  positive pay data as **files** on a schedule (issued items in, exceptions and decisions
  out) as much as by API.

So an SDK alone isn't enough. OpenClear needs a few platform features first (Phase P0),
then client libraries, an embeddable experience and a core integration kit on top.

## What we'll build

1. **Platform foundations (P0, in OpenClear itself):**
   - **API clients for a bank's systems:** OAuth 2.0 client credentials, managed by a
     bank admin, scoped to permissions and optionally to one customer, rotated, audited.
     This replaces "log in as a user with a password".
   - **Acting for a customer's user:** an OBP's server acts for one of its signed-in
     business users, so decisions are attributed to a real person, the maker-checker
     rules hold, and the audit log names them. Users are matched by an external id and
     created on first use, within limits the bank sets.
   - **Webhooks:** signed events following the
     [Standard Webhooks](https://www.standardwebhooks.com/) spec (`webhook-id`,
     `webhook-timestamp`, `webhook-signature`). Delivered by the job queue with retries;
     the bank can see a delivery log and replay. Events:
     - `exception.created`
     - `exception.deadline_approaching`
     - `exception.decided`
     - `bulk_upload.completed`
     - `decision_batch.ready`
   - **A decision feed for the core:** `GET /api/v1/decisions?after=<cursor>` returns every
     decision since the cursor, in order, with what the core needs to pay or return each
     item (return reason, ACH return code).
     - A scheduled **decision file** goes into an outbox directory or bucket:
       configurable CSV and fixed-width layouts, with acknowledgement so nothing is
       missed or sent twice.
   - **Idempotency keys** (`Idempotency-Key` header) on every POST, so a retried call
     never creates an item twice.
   - **Upserts by external id** for accounts and customers, so a core can sync them
     nightly without a lookup per row.
   - **A versioned OpenAPI contract:** the spec is published with each release and checked
     in CI for breaking changes; `/api/v1` changes only additively, with a written
     deprecation policy.
2. **Client SDKs (P1):** generated from the OpenAPI spec, with a thin hand-written layer
   for:
   - token handling;
   - retries with idempotency;
   - page iterators;
   - bulk helpers (split large batches, follow the 202 and poll);
   - webhook verification;
   - typed errors.
   
   Python and TypeScript first, then .NET (P3).
3. **Embedded experience (P2):**
   - **Sign-on handoff:** from the OBP into OpenClear for a customer's users, via OIDC
     with the OBP as identity provider, or a signed launch token.
   - **Embed mode:** OpenClear's customer pages without the side navigation, framed only by
     origins the bank allowlists.
   - **`@openclear/embed`:** a small JS library that mounts the frame, sizes it, and
     passes events (an exception decided, a session ending) to the host page.
   - **Reference plugins:** for Banno (a plugin card), Alkami (an SDK provider) and Q2 (an
     SDK extension).
4. **Core integration kit (P3):**
   - reference adapters (presented items in, decisions out, account sync) with
     configurable file layouts;
   - the .NET SDK;
   - a command-line tool (`openclear push`, `openclear decisions pull`,
     `openclear webhooks tail`).
5. **Sandbox and certification (P4):**
   - a self-service sandbox bank per vendor;
   - synthetic data from [CheckSim](../CheckSim) (synthetic checks with a ground-truth
     manifest) and the volume seeder;
   - a conformance test suite an integration must pass;
   - a docs site with guides per integrator.

## Phases

| Phase | What | Size | Why this order |
|---|---|---|---|
| P0 | API clients (OAuth client credentials), acting for a customer user, idempotency keys, decision feed and decision files, webhooks, external-id upserts, OpenAPI publishing and breaking-change check | L | Every SDK and integration depends on these |
| P1 | Python and TypeScript SDKs (generated plus a thin layer), webhook verification, examples, published from their own repos | M | The two languages the first OBPs use (Q2's SDK is Python; Banno plugins are web apps) |
| P2 | Sign-on handoff for customer users, embed mode with an origin allowlist, `@openclear/embed`, reference plugins for Banno, Alkami and Q2 | L | Lets a bank offer OpenClear inside its digital banking without the OBP rebuilding screens |
| P3 | Core integration kit: decision files and outbox, presented-item and account-sync adapters, .NET SDK, CLI | M–L | Cores and middleware are mostly .NET and file-driven |
| P4 | Sandbox per vendor, CheckSim-driven test data, conformance suite, docs site, certification checklist | M | Turns integrations into a repeatable product (and a consulting deliverable) |

### P0 detail (the first branch)

- [ ] `api_client` table: bank, name, client id, secret hash (or a registered public key
      for `private_key_jwt`), scopes (a subset of the permission catalog), optional
      customer, optional "may act for users", created/rotated/last used, revoked. Managed
      under Settings → API clients; every change audited.
- [ ] `POST /api/v1/oauth/token` (client credentials) issuing short-lived access tokens
      (the existing ECDSA JWT keys), plus rate limits per client.
- [ ] Acting for a user: a client allowed to may send `OpenClear-Act-As: <external user
      id>` (or use RFC 8693 token exchange). The user is a membership bound to the
      client's customer, matched by `external_id`, and can be created on first use with a
      security group the bank picks. Decisions and audit entries carry both the user and
      the client.
- [ ] `Idempotency-Key` on POST: stored per client for 24 hours with the request hash and
      response; a repeat returns the stored response, and a different body with the same
      key gets 422.
- [ ] Decision feed: a per-bank, monotonically increasing `decision.feed_sequence`; `GET
      /api/v1/decisions?after=` (paged); `POST /api/v1/decisions/acknowledge` so a core can
      confirm what it acted on. Decision files: CSV first, with a layout per bank.
- [ ] Webhooks: `webhook_endpoint` (URL, events, secret, active), `webhook_delivery` (event,
      attempts, response code, next try). Delivered as background jobs with exponential
      back-off for 24 hours, then failed with a visible reason. Payloads carry ids, not
      data, so a receiver fetches through the API with its own permissions. Settings →
      Webhooks shows deliveries and can replay one.
- [ ] Upserts: `PUT /api/v1/accounts/by-external-id/{id}` and the same for customers.
- [ ] OpenAPI: export the spec at release, commit it as `openapi/openclear-v1.json`, and
      fail CI on a breaking change (a removed path or field, a narrowed type).
- [ ] Fix while there: the API reports version 1.5.0 (`main.py`); read it from the package.

## Decisions (recommended; adopted per the standing instruction, change any of them)

| # | Question | Recommendation | Alternatives |
|---|---|---|---|
| X1 | SDK license | **Apache-2.0**, in separate repos (`openclear-sdk-python`, `-js`, `-dotnet`), so a proprietary core or OBP can ship the SDK without AGPL obligations. OpenClear itself stays AGPL | MIT (no patent grant); AGPL (would put off every vendor) |
| X2 | Machine authentication | **OAuth 2.0 client credentials** (secret or `private_key_jwt`), short-lived JWTs | Static API keys (simpler, worse rotation and leakage story); mTLS (strong, heavy for a first integration) |
| X3 | Acting for a customer's user | **An act-as header limited to clients the bank allows, users matched by external id, created on first use within the bank's chosen group** | Every user signs in to OpenClear themselves (breaks the native OBP experience) |
| X4 | Webhook format | **Standard Webhooks** (HMAC-SHA256, id and timestamp headers), thin payloads | Full payloads (leak data to a misconfigured endpoint); CloudEvents (heavier) |
| X5 | First SDK languages | **Python and TypeScript**, then .NET | .NET first (cores), Java |
| X6 | Generated or hand-written | **Generated models and calls from OpenAPI, with a thin hand-written layer** | Fully hand-written (drifts from the API) |
| X7 | Embedding | **Embed mode with a per-bank origin allowlist, plus sign-on handoff**; recommend the bank host OpenClear on a subdomain of its digital banking domain (avoids third-party cookie problems) | No embedding: native screens only, through the API |
| X8 | First OBP reference | **Banno** (public plugin docs and a positive pay API to align with), then Alkami | Q2 first (Python SDK, but positive pay is already native there) |
| X9 | Decision delivery to cores | **Both: a pull feed with acknowledgement, and scheduled files to an outbox** | Files only; API only |

## Open questions for you

- Do you have a first bank, core or OBP in mind? That decides X8 and which file layouts
  P3 builds first.
- Should sandbox access (P4) be free and self-service, or part of a paid onboarding
  engagement (business plan)?

## Progress log

(Newest last.)
- 2026-10-09 Research done; plan written.

## Sources

- Q2 platform extensibility and SDK: https://www.q2.com/products/digital-banking/platform/platform-extensibility ·
  https://www.q2.com/product/marketplace
- Alkami SDK integration types: https://docs.developer.alkami.com/sdk/basics/integration-types/ ·
  https://alkami.com/digital-banking-solutions/retail-banking/banking-apis-and-sdks
- Banno Plugin Framework (sandboxed iframe, external applications, CSP, hosting advice):
  https://jackhenry.dev/open-api-docs/plugins/overview/ ·
  https://jackhenry.dev/open-api-docs/plugins/architecture/restrictions/ ·
  https://jackhenry.dev/open-api-docs/plugins/architecture/hosting/
- Jack Henry Positive Pay consumer API: https://jackhenry.dev/open-api-docs/consumer-api/api-reference/v0/positive-pay/
- Jack Henry jXchange and the Vendor Integration Program: https://supergood.ai/docs/jack-henry-silverlake-api ·
  https://www.businesswire.com/news/home/20231011142292/en
- Standard Webhooks: https://www.standardwebhooks.com/ · https://www.svix.com/resources/glossary/webhook-signature/
