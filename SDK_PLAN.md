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

## Open source building blocks (decided 2026-10-09)

- **[Apache Fineract](https://github.com/apache/fineract)** (Apache-2.0), the open source
  core banking platform behind Mifos X: customers, savings and current accounts,
  transactions and accounting over REST, with external business events on Kafka or
  ActiveMQ. Docker Compose files ship with it (Postgres or MariaDB).
  - **The sandbox core (P4):** each sandbox bank gets its customers and accounts from a
    Fineract instance, so a vendor can test end to end without a Fiserv or Jack Henry
    contract.
  - **The first core adapter (P3):**
    - sync Fineract customers and accounts into OpenClear by external id (REST, then
      events);
    - turn presented items (from CheckSim) into Fineract withdrawals;
    - post pay decisions back, and leave returns unposted or reverse them.
  - It's the reference for what an adapter does, not proof against a production US core:
    Fineract's users are mostly in microfinance, and it doesn't clear checks or ACH.
- **[Moov](https://github.com/moov-io)** (Apache-2.0): ACH and Image Cash Letter (X9)
  libraries and servers.
  - **Already used (2026-10-09):** to check OpenClear's NACHA and X9.37 parsers against a
    reference. That found and fixed real gaps: standard cash letters weren't readable, and
    most valid ACH files were rejected. Their sample files are now test fixtures.
  - **Next:**
    - generate realistic presentment files for the sandbox (X9 from CheckSim images,
      NACHA debits);
    - write the decision and return files a core takes back (P3);
    - validate a vendor's files in the conformance suite (P4).

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
   - **Reference integration:** Mifos Mobile (open source) on the Fineract sandbox first;
     Banno (a plugin card), Alkami (an SDK provider) and Q2 (an SDK extension) when a bank
     asks for one.
4. **Core integration kit (P3):**
   - reference adapters (presented items in, decisions out, account sync) with
     configurable file layouts, starting with Apache Fineract;
   - decision and return files written with Moov's ACH and X9 libraries;
   - the .NET SDK;
   - a command-line tool (`openclear push`, `openclear decisions pull`,
     `openclear webhooks tail`).
5. **Sandbox and certification (P4):**
   - a self-service sandbox bank per vendor, backed by an Apache Fineract core;
   - synthetic data from [CheckSim](../CheckSim) (synthetic checks with a ground-truth
     manifest) and the volume seeder, packaged as real X9.37 and NACHA files with Moov;
   - a conformance test suite an integration must pass;
   - a docs site with guides per integrator.

## Phases

| Phase | What | Size | Why this order |
|---|---|---|---|
| P0 | API clients (OAuth client credentials), acting for a customer user, idempotency keys, decision feed and decision files, webhooks, external-id upserts, OpenAPI publishing and breaking-change check | L | Every SDK and integration depends on these |
| P1 | Python and TypeScript SDKs (generated plus a thin layer), webhook verification, examples, published from their own repos | M | The two languages the first OBPs use (Q2's SDK is Python; Banno plugins are web apps) |
| F | Federation: SAML 2.0, SCIM 2.0 provisioning, back-channel logout, IdP-initiated sign-in, and an OBP sign-on profile for customer users | M–L | Banks' identity providers and OBP handoffs still often use SAML; directory changes must reach OpenClear; P2 builds on the OBP profile |
| P2 | Sign-on handoff for customer users, embed mode with an origin allowlist, `@openclear/embed`, reference plugins for Banno, Alkami and Q2 | L | Lets a bank offer OpenClear inside its digital banking without the OBP rebuilding screens |
| P3 | Core integration kit: decision files and outbox (Moov), presented-item and account-sync adapters (Apache Fineract first), .NET SDK, CLI | M–L | Cores and middleware are mostly .NET and file-driven |
| P4 | Sandbox per vendor on an Apache Fineract core, CheckSim and Moov test files, conformance suite, docs site, certification checklist | M | Turns integrations into a repeatable product (and a consulting deliverable) |

### F detail (federation)

What exists (2026-10-09): OIDC sign-on per bank and per business customer, generic across
providers (Okta, Azure AD, Ping, Keycloak, Google), with group-to-security-group
mapping, optional provisioning on first sign-in, and password sign-in switchable off per
bank. PKCE is added as a fix ahead of this phase.

- [x] **SAML 2.0** (2026-10-08, branch `federation-f`; `auth/saml_service.py`,
      `web/routers/saml.py`, migration `c8e0a2d4f6b8`): a second connection type beside
      OIDC, with the same group mapping, provisioning and per-bank and per-customer scoping.
  - SP metadata per connection, and a key pair generated per connection (requests
    signed; encrypted assertions accepted).
  - **What an assertion must be:** signed, SHA-256 or better, for our audience and ACS, and
    within its validity window.
  - **Matching answers to requests:** InResponseTo must match the request named in a signed
    `SameSite=None` state cookie, whose RelayState must come back. That's because the
    identity provider's POST is cross-site, and RelayState is limited to 80 bytes.
  - Each assertion is accepted once (`sso_assertion_seen`).
  - `sso_identity` records the provider's subject for each person.
  - **Tests:** a simulated identity provider signs real assertions. Tampering, an unsigned
    assertion, the wrong audience, expiry, someone else's request, an unsolicited answer,
    a wrong RelayState and a replay are each refused for the right reason.
- [x] **SCIM 2.0** (2026-10-08; `services/scim_service.py`, `api/scim.py`, migration
      `d9f1b3c5e7a9`): `/scim/v2/Users`, `/Groups`, `ServiceProviderConfig`,
      `ResourceTypes` and `Schemas`, per connection.
  - **Authentication:** a bearer token embedding the bank and connection ids, so it binds
    under RLS before anything is read. It's shown once, and only its hash is stored.
  - **Access:** a person's SCIM groups go through the connection's group mappings, as a
    sign-in's do. With no match or `active: false`, their membership here is
    deactivated and their sessions end.
  - Deactivation, never deletion. People are matched to existing users by email. An
    email can't change if the person belongs to other banks.
  - **Filters:** `eq` only.
  - **Tests:** Okta-style and Entra-style requests, a customer-scoped connection, linking
    an existing user, and RLS isolation.
- [x] **Back-channel logout** (2026-10-08): signing out at the identity provider ends the
      person's OpenClear sessions.
  - **OIDC Back-Channel Logout 1.0** (`web/routers/oidc_federation.py`): the logout token
    is verified like an ID token, plus the logout event, no nonce and a recent `iat`.
    Each `jti` is accepted once. The person is found by `sub` through `sso_identity`;
    session-only (`sid`) logout isn't supported, since OpenClear doesn't track provider
    sessions.
  - **SAML single logout:** signed LogoutRequests only.
  - Either way, the person's sessions end everywhere.
- [x] **IdP-initiated sign-in** (2026-10-08):
  - **OIDC third-party initiated login:** the `/initiate` URL checks `iss`, keeps
    `target_link_uri` on this site, passes `login_hint` through, and starts a normal
    PKCE sign-in.
  - **SAML IdP-initiated:** opt-in per connection, and each assertion is accepted once.
- [x] **OBP sign-on profile** (2026-10-09; migration `e0a2c4e6a8b1`): a bank-wide
      connection with a **customer claim** (or SAML attribute), whose people belong to
      business customers. The claim names the person's customer, and their session is
      scoped to it. A missing or unknown customer gets no access.
  - **RFC 8693 token exchange** (`/api/v1/oauth/token`): the provider's backend trades
    a person's ID token for a client token carrying an `obo` claim.
    - Each request re-checks the person's membership and intersects their permissions
      with the client's, through the same path as act-as.
    - The token expires no later than the ID token.
    - The person is created on first use. This covers the item moved here from P0.
  - **`OpenClear-Act-As-Subject`:** act for someone by the provider's own id, through
    `sso_identity`.
  - **API clients** can be linked to a connection (`api_client.sso_connection_id`).
  - **Tests:** the customer claim, the exchange, intersected permissions, revocation
    re-checked, eight refusal cases, act-as by subject, and the exchange under RLS on
    Postgres.
  - Mifos itself comes in P2's reference integration.

### P3 detail (core integration kit)

- [x] **Decision outbox** (2026-10-09; `services/decision_outbox_service.py`, migration
      `a2c4e6b8d0f3`): per-bank settings (on or off, layout, every 5 to 1,440 minutes,
      and each layout's settings).
  - Each run writes the decisions since the last batch to the `decision_files` storage
    area, records a batch with its SHA-256, and sends `decision_batch.ready`.
  - The core downloads and acknowledges each batch (`/api/v1/decision-batches`).
    Acknowledging moves the feed's `outbox` consumer as far as acknowledged batches are
    contiguous.
  - Runs by an opt-in scheduler tick, bank by bank under RLS, or on demand.
  - Decision layouts now take per-bank settings (`config_fields`, `write(feed, settings)`;
    the plugin API wasn't released yet).
- [x] **NACHA return file** (`networks/ach/returns_file.py`, layout `nacha_returns`):
  - return entries 26/36 with type 99 addenda (R code and original trace);
  - batches per company, SEC code and effective date;
  - the ODFI taken from the original trace number;
  - **validated by Moov's ACH server** in the tests (`moov/ach`, skipped if it isn't
    running).
  - The default return reasons are now seeded with their R codes, and existing banks'
    unset defaults are filled in (migration `b3d5f7a9c1e4`).
  - The feed gains `account_name`, for the return entry's name.
- [ ] Check returns as an X9.37 return file (Moov imagecashletter): next, if a bank needs
      it; check returns are in the CSV layout until then.
- [x] **CLI** (2026-10-09; `openclear` in the Python SDK, `openclear_sdk/cli.py`): `push`,
      `decisions pull` (JSONL or CSV, acknowledged), `decisions files` (SHA-256 checked,
      acknowledged), and `webhooks tail` (verifying receiver). Exit codes are 0, 1 or 2.
      Tested against the app.
- [ ] Fineract adapter, .NET SDK: next.

### P2 detail (embedded experience)

- [x] **Sign-on handoff** (2026-10-09): the online banking server, acting for the
      signed-in person, asks `POST /api/v1/embed/launch` for a single-use launch URL.
  - It acts through token exchange (phase F) or act-as.
  - The URL is good for 60 seconds and once (`embed_launch`, migration `f1b3d5f7a9c2`).
  - It starts an embedded session for that person, with intersected permissions and the
    frame's origin in the session (`emb` claim, kept through refresh).
  - This avoids sending identity-provider redirects through an iframe, which many
    providers block.
- [x] **Embed mode:** the bank lists allowed origins (Settings → Embedding).
  - An embedded session's pages drop the navigation.
  - They're framed only by the session's origin, while the bank still lists it
    (`frame-ancestors`). Ordinary sessions stay `'none'`/`DENY`.
  - **Cookies:** `SameSite=None; Secure; Partitioned` over HTTPS. Partitioned is written
    by hand, since Starlette needs Python 3.14 for it.
  - **Ending:** an expired, signed-out or revoked embedded session lands on
    `/ui/embed/ended`, the only page any site may frame. It tells the parent.
- [x] **`@openclear/embed`** (`sdks/embed`, Apache-2.0, no dependencies):
  - `mount()` sizes the frame and passes events (`navigated`, `exception-decided`,
    `exception-recommended`, `session-ended`);
  - it only trusts messages from the frame's own window and origin;
  - `navigate()` relaunches.
- [x] **Reference:**
  - `docs/guides/embedding.md`, including mobile WebViews and Mifos specifics;
  - `examples/embed-host` (a stand-in online banking page);
  - SDKs gain `embed.create_launch` from the contract.
- [ ] Reference plugins for Banno, Alkami and Q2: when a bank asks (X8). Mifos Mobile
      integration code lives in Mifos; the guide covers the WebView flow it needs.

### P1 detail (SDKs)

- [x] **Generator** (2026-10-08): `scripts/generate_sdks.py` turns the OpenAPI contract
      into each SDK's models (TypedDicts / interfaces) and one class per API area with a
      method per endpoint, plus `iter_` methods for paged lists that follow `Link: rel="next"`.
      A custom generator rather than openapi-generator: no Java or third-party templates,
      and output that reads like hand-written code. `tests/test_sdk/test_generated_up_to_date.py`
      fails when it's stale.
- [x] **Python** (`sdks/python`, `openclear-sdk`, httpx only), with these features:
      - tokens (renewed before expiry, and once on a 401);
      - retries with one idempotency key per call;
      - paging, with next links pinned to the configured host;
      - `bulk.submit` (batches, the 202 path, results back in the original order) and
        `bulk.upload_file`;
      - `decision_feed.consume`;
      - `webhooks.verify` and `webhooks.sign`;
      - `acting_as`;
      - typed errors.

      Tests run against the app (`test_python_sdk.py`), with a mock transport for the
      retry cases.
- [x] **TypeScript** (`sdks/typescript`, `@openclear/sdk`), with the same features. It
      depends only on fetch and Web Crypto, and ships as ESM with type declarations.
      Node 22.18+ runs the tests without a build step: unit tests use a fake fetch, and
      live tests run against a real server started by `test_typescript_sdk.py`.
- [x] Examples: a core decision job and webhook receivers (Python, TypeScript).
- [x] Fixed on the way: file downloads (CSV, XML, decision files) are declared as files in
      the contract, so generated clients return bytes.
- [ ] **Publishing from their own repos** (`openclear-sdk-python`, `openclear-sdk-js`) to
      PyPI and npm: waiting on approval. It creates public repositories and package names.
      Until then they live in `sdks/`, Apache-2.0.

### P0 detail (the first branch)

- [x] **API clients** (2026-10-09, branch `sdk-p0`): `api_client` table (migration
      `c1e3a5b7d9f0`, RLS). Each client acts through a **service identity**: a user that
      can't sign in, with one membership carrying a security group the bank picks (its
      "scopes") and an optional customer. So permission checks, customer scope, RLS and
      audit attribution are the existing ones. Client ids embed the bank's id, so the token
      endpoint binds to the right bank before reading anything. Managed under Admin → API
      clients (`api_client:manage`); create, rotate and revoke are audited; the secret is
      shown once. `private_key_jwt` is left for later.
- [x] `POST /api/v1/oauth/token` (client credentials, RFC 6749 errors, form or Basic auth)
      issuing 30-minute access tokens with the existing ECDSA keys, rate-limited per
      address. Tests: `tests/test_api/test_api_clients.py`,
      `tests/test_postgres/test_rls_api_clients.py`.
- [x] Acting for a user (2026-10-09): a client the bank allowed (`can_act_for_users`) sends
      `OpenClear-Act-As: <email>` (and `OpenClear-Act-As-Customer` if needed). Existing,
      active members only (of the client's customer, if any); permissions are the
      intersection; the action's audit entry names the person and an `api_client.act_as`
      entry names the client (writes). **Moved to phase F:** matching by the provider's own
      user id and creating the person on first use, which need the provider's verified
      identity (the OBP sign-on profile); RFC 8693 token exchange likewise.
- [x] `Idempotency-Key` on POST, PUT, PATCH and DELETE (2026-10-09; `api/idempotency.py`,
      migration `d2f4b6c8e0a1`, RLS): per caller, 24 hours, replay with
      `Idempotency-Replayed: true`, 422 for a different request, 409 while in flight, 5xx
      not kept (an unhandled error clears the key). Tests: `tests/test_api/test_idempotency.py`,
      plus a replay under RLS.
- [x] Decision feed (2026-10-09; migration `e3a5c7d9f1b2`): per-bank `decision.feed_sequence`,
      assigned under a transaction-scoped lock so numbers become visible in order (tested
      with eight concurrent deciders on Postgres); existing decisions numbered by the
      migration. `GET /api/v1/decisions?after=` (paged, `X-Latest-Sequence`), `/feed.csv`,
      `POST /acknowledge` (forward only) and `/consumers` (lag). Per-bank decision-file
      layouts and the scheduled outbox move to PL2/P3, where layouts become plugins.
- [x] Webhooks (2026-10-09; `services/webhook_service.py`, migration `f4b6d8e0a2c3`, RLS):
      Standard Webhooks signing, thin payloads, events written in the caller's transaction
      (outbox), delivered as jobs on Svix's retry schedule (about a day), then failed.
      Admin → Webhooks: endpoints, recent deliveries, test event, replay, rotate secret.
      SSRF-safe (public https, checked at connect time, no redirects;
      `webhook_allow_private_hosts` for single-bank installs). Events: `exception.created`,
      `exception.decided`, `bulk_upload.completed`. **Not yet:**
      `exception.deadline_approaching` (needs a "warned" marker per exception) and
      `decision_batch.ready` (with the outbox in P3). Tests against a real local receiver,
      including DNS rebinding and a delivery under RLS.
- [x] Upserts (2026-10-09): `PUT /api/v1/customers/{customer_number}` and
      `PUT /api/v1/accounts/{account_number}`, with paged lists. Keyed by the bank's own
      numbers rather than a new external id: cores know accounts by number, and
      `external_account_id` already means the customer's own reference. A bank can use its
      core's CIF as the customer number.
- [x] OpenAPI (2026-10-09): `scripts/export_openapi.py` writes `openapi/openclear-v1.json`
      (API routes only; web pages left out of the schema). `tests/test_openapi_contract.py`
      fails on a breaking change (removed endpoint, newly required parameter or body field,
      removed response field) and on an unrefreshed additive change, comparing the API's
      shape rather than the file text so library updates don't break it. Type narrowing
      isn't detected yet.
- [x] Fix while there: the API reports version 1.5.0 (`main.py`); read it from the package
      (done in 1.7.0).

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
| X8 | First OBP reference | **Decided 2026-10-09: only an open source online banking provider.** That's **Mifos Mobile** (openMF/mifos-mobile, MPL-2.0, active), the customer app for Apache Fineract, so it pairs with the Fineract sandbox core. Commercial OBPs (Banno, Alkami, Q2) follow when a bank asks | Banno first (public plugin docs) |
| X9 | Decision delivery to cores | **Both: a pull feed with acknowledgement, and scheduled files to an outbox** | Files only; API only |

## Answered

- First integration: only an open source online banking provider, so Mifos Mobile on the
  Fineract sandbox (X8). File layouts in P3 start with Fineract's.
- Sandbox access (P4) is **free and self-service**, like everything else (2026-10-09).
- Extending OpenClear without merging: a plugin framework, planned in PLUGIN_PLAN.md
  (PL1 alongside P0). Closed-source vendors integrate out of process through P0's
  webhooks and API; in-process plugins are AGPL.

## Progress log

(Newest last.)
- 2026-10-09 Research done; plan written.
- 2026-10-09 Apache Fineract (sandbox core, first adapter) and Moov (file formats) adopted.
- 2026-10-09 Decided: first OBP reference is open source only (Mifos Mobile); the sandbox is
  free. PLUGIN_PLAN.md written; PL1 runs alongside P0.
- 2026-10-09 P0 started (branch `sdk-p0`): API clients and the token endpoint done;
  idempotency keys done; decision feed done; webhooks done; customer and account sync done; API contract published and checked; acting for a user done. **P0 complete.**
- 2026-10-08 P0 merged into `main`. All three suites were green: SQLite 1315 passed, Postgres 1335, Linux 3.11 1309.
- 2026-10-09 Federation phase (F) added: SAML 2.0, SCIM 2.0, back-channel logout,
  IdP-initiated sign-in, OBP sign-on profile. PKCE for the existing OIDC sign-on done as a
  fix ahead of it.
  Moov's reference implementations already used to fix the X9.37 and NACHA parsers
  (branch `x937-standard`).

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
- Apache Fineract: https://github.com/apache/fineract · https://hub.docker.com/r/apache/fineract ·
  https://intelligrow.co/blog/mifos-and-apache-fineract-explained/
- Mifos Mobile (MPL-2.0): https://github.com/openMF/mifos-mobile · Mifos X web app: https://github.com/openMF/web-app
- Moov ACH and Image Cash Letter: https://github.com/moov-io/ach · https://github.com/moov-io/imagecashletter ·
  https://www.redhat.com/en/blog/moov-to-digital-banking-on-openshift
- 2026-10-08 **P1 done** (branch `sdk-p1`): Python and TypeScript SDKs, generator, examples. Publishing to PyPI and npm from their own repositories waits on approval.
- 2026-10-08 P1 merged into `main`. All three suites were green: SQLite 1338 passed, Postgres 1360, Linux 3.11 1331.
- 2026-10-09 **P2 done** (branch `federation-f`, after F): embedded sessions from one-time launch links, origin-limited framing, partitioned cookies, `@openclear/embed`, embedding guide and example host.
