# OpenClear behind a WAF or CDN (Cloudflare, Akamai)

Banks rarely expose an application straight to the internet. OpenClear will usually sit
behind an edge network that provides a WAF, DDoS protection, bot management and caching:
Cloudflare or Akamai most often, sometimes AWS CloudFront with AWS WAF, Azure Front Door,
or an F5 in the bank's own data centre. This plan makes OpenClear work correctly, securely
and fast in that setup, and gives operators a tested configuration for each of the main
providers.

## What exists today

| Area | Today | Where |
|---|---|---|
| Caller's IP | Trusts the TCP peer. With `OPENCLEAR_TRUSTED_PROXY_COUNT=N`, takes the Nth entry from the right of `X-Forwarded-For` instead. | `web/client_ip.py` |
| Rate limiting | One per-IP limit for every request (120/minute), plus stricter limits on a few routes (markdown preview, OAuth token). Counts are kept in memory, or shared in Redis. | `main.py`, `web/rate_limit.py` |
| Request size | `Content-Length` is checked against `max_request_body_bytes` (50 MB). A chunked body without a length isn't checked. | `main.py` |
| Response headers | A strict CSP with a per-response nonce, `X-Frame-Options: DENY` (left off for embedded sessions, which use `frame-ancestors`), HSTS on https deployments, and `Cache-Control: no-store` on `/ui` and `/api`. | `web/security_headers.py` |
| Public URLs | `OPENCLEAR_PUBLIC_BASE_URL` overrides the host and scheme for SSO callbacks, SAML metadata and the SCIM URL. Without it they come from the request. | `auth/saml_service.py`, `web/routers/auth.py` |
| Static files | Versioned by file mtime (`?v=`), served with `Last-Modified` and `ETag` but no `Cache-Control`. | `web/templates.py` |
| Server | uvicorn. The main image uses its defaults: proxy headers trusted from 127.0.0.1 only, 5-second keep-alive. The sandbox trusts forwarded headers from any address. | `Dockerfile`, `Dockerfile.sandbox` |
| Health | `GET /health` | `main.py` |
| Docs | A README section on `OPENCLEAR_TRUSTED_PROXY_COUNT`. | `README.md`, "Reverse proxy / WAF deployment" |

## What goes wrong behind an edge today

These come from reading the code against how Cloudflare and Akamai behave. W0 confirms
each one on a real zone before anything is built.

1. **One rate-limit bucket for the whole bank.** If `OPENCLEAR_TRUSTED_PROXY_COUNT` is left
   at 0, every request appears to come from a handful of edge addresses. At 120 requests a
   minute per address, a busy morning of decisions starts returning 429s to everyone.
2. **Counting hops doesn't fit Akamai.** A request can pass through a variable number of
   Akamai servers (edge, parent, SureRoute), each appending to `X-Forwarded-For`, so no
   fixed count is right. Cloudflare's chain is fixed (one hop) unless the bank adds its own
   load balancer behind it. Both providers send the real client's address in a dedicated
   header: `CF-Connecting-IP` and `True-Client-IP` (Akamai, and Cloudflare Enterprise). That
   header should be trusted only when the connection comes from the provider's own
   addresses.
3. **The origin can be reached directly.** Nothing stops someone who finds the origin's
   address from bypassing the WAF altogether. The edge's protection, and its view of the
   caller's IP, are then worth nothing.
4. **Requests the WAF will likely block.** Managed rulesets (Cloudflare Managed Rules,
   the OWASP Core Rule Set on both; Akamai App & API Protector) are tuned for generic web
   apps. OpenClear legitimately sends:
   - **Binary X9.37 uploads:** EBCDIC text and TIFF images, in multipart bodies up to 50 MB.
     Likely triggers: multipart and binary-content rules, file-type restrictions, and body
     inspection limits.
   - **SAML responses:** large base64 XML POSTed to the assertion consumer URL by the
     bank's identity provider.
   - **SCIM:** filters like `userName eq "a@b.com"` and PATCH operations. These look like SQL
     injection to some rules.
   - **URLs as values:** webhook endpoints, SSO issuer URLs and embedding origins. These match
     remote file inclusion rules (CRS 931).
   - **Markdown:** banner messages and documentation previews, which may contain HTML-like
     text. These match XSS rules.
   - **JSON API traffic from the bank's core:** high volume from a few addresses. This trips
     rate-based and bot rules.
5. **Bot management challenges machines.** JavaScript challenges and CAPTCHAs (Cloudflare
   Bot Fight Mode, Super Bot Fight Mode; Akamai Bot Manager) can't be passed by the core's
   API client, the SDKs, the directory's SCIM client, an identity provider's back-channel
   logout or SAML POST, or a webhook test probe.
6. **Edge features that rewrite pages break the CSP.** Several edge features inject scripts
   that OpenClear's nonce-based CSP correctly blocks, leaving console errors and broken
   behaviour:
   - Cloudflare Rocket Loader, Email Address Obfuscation (which also mangles email
     addresses on the Users page), Zaraz, the Web Analytics beacon, and JavaScript
     challenges;
   - Akamai mPulse injection.

   Cloudflare's "Add security headers" managed transform adds `X-Frame-Options: SAMEORIGIN`,
   which breaks embedding in online banking.
7. **Timeouts.** Cloudflare gives up on an origin after 100 seconds (error 524); Enterprise
   plans can raise this. Akamai's origin read timeout defaults to about 120 seconds. These
   requests do their work while the browser waits, and can run past that:
   - replaying a large file (up to 20,000 items);
   - an evidence export covering a long period;
   - the Controls page and the action-log "Verify" button, which both walk the whole log.
8. **Keep-alive mismatch.** uvicorn closes idle connections after 5 seconds; edges keep
   connections to the origin open far longer. When the origin closes a connection the edge
   is about to reuse, users get sporadic 502 or 520 errors.
9. **Scheme and host.** Without `OPENCLEAR_PUBLIC_BASE_URL`, URLs built from the request use
   `http://` and the origin's own host name when TLS ends at the edge. That breaks SSO
   callbacks, SAML metadata and sandbox links.
10. **Edge retries of POSTs.** Some edge configurations retry a request on an origin error.
    API writes are protected by `Idempotency-Key`; web form posts aren't.
11. **Static files aren't cached well.** Without `Cache-Control`, edges and browsers use
    heuristics or revalidate on each load. The `?v=` versioning makes long, immutable
    caching safe, but the server never says so.
12. **The action log doesn't record the caller's IP.** Examiners usually expect a source
    address with each security-relevant action. Behind an edge, that has to be the real
    client's address (W1), not the edge's.
13. **No way to tie a request to the edge's log.** When a WAF blocks or an origin errors,
    support needs the edge's request id (`CF-Ray`; Akamai's reference number) next to
    OpenClear's own logs. Nothing records it today.

## Phases

| Phase | What | Notes |
|---|---|---|
| W0 | **Confirm on real zones.** Put the sandbox behind a Cloudflare zone (free or Pro plan) and, if an Akamai trial or partner account is available, an Akamai property. Run the edge check (W4) and a session of real use: upload X9.37 and NACHA files, sign in with SAML and OIDC, provision via SCIM, embed, export evidence. Record what each finding above actually does. | Findings that don't reproduce are dropped, and new ones are added. |
| W1 | **Real client IP.** New settings: `OPENCLEAR_CLIENT_IP_HEADER` (e.g. `CF-Connecting-IP`, `True-Client-IP`) and `OPENCLEAR_TRUSTED_PROXY_CIDRS` (the edge's address ranges). OpenClear trusts the header only when the connection's own address is in those ranges; otherwise it uses the connection's address. `OPENCLEAR_TRUSTED_PROXY_COUNT` stays for plain reverse proxies. `scripts/edge_ip_ranges.py cloudflare` prints Cloudflare's published ranges in the right format (Akamai's Site Shield ranges are specific to each customer, so they're entered by hand). Both places that use the caller's IP already go through `get_client_ip`: the rate limits and the signer's IP on a WSUD. | Fix uvicorn's own proxy-header trust to match, so `request.client` can't be spoofed either. The sandbox's `--forwarded-allow-ips='*'` is replaced. |
| W1 | **Rate limits that fit an edge.** Authenticated API traffic is limited per API client or person, not per IP, so one core system or one branch's NAT address isn't throttled for everyone. Per-IP limits remain for the endpoints that don't sign anyone in: password sign-in, the OAuth token, embed launch, SCIM authentication. The defaults are reviewed against W0's real traffic. | Edge rate-limiting rules for the same endpoints go in the guide (W5), as the first line; OpenClear's limits are the backstop. |
| W2 | **Origin lockdown.** Recommended ways to accept traffic only from the edge, in order of preference: (1) **Cloudflare Tunnel**, so the origin has no inbound port at all; (2) mutual TLS from the edge (Cloudflare Authenticated Origin Pulls; Akamai origin certificates or mTLS), at the bank's load balancer; (3) the edge's address ranges at the firewall (Akamai Site Shield, Cloudflare ranges). Each can also add a **shared-secret header**: with `OPENCLEAR_EDGE_SECRET_HEADER` and `OPENCLEAR_EDGE_SECRET`, OpenClear refuses (403) any request without it, except `/health`. The comparison is constant-time, and two secrets may be set while one is being rotated. | The shared secret is defence in depth on top of (1)–(3), not instead of them. |
| W2 | **Edge awareness on the Controls page.** A new control, "Edge in front": it reads which edge headers arrive (`CF-Ray`, `CF-Connecting-IP`, `True-Client-IP`, `Akamai-Origin-Hop`, `Via`) and checks them against the configuration. **FAIL** if an edge is evidently in front but the client IP isn't configured (finding 1). **WARN** if there's no origin lockdown (no edge secret, and no statement in `OPENCLEAR_EDGE_ATTESTATION` for tunnel or mTLS). **PASS** with the evidence otherwise. Plus a diagnostic for admins: how this request's client IP was worked out, and from which header. | It joins the evidence export (C3) automatically. |
| W2 | **Source IP in the action log.** Each entry records the caller's IP (resolved per W1) in its signed metadata, and the action log page and evidence export show it. |  |
| W2 | **Request ids.** When the edge is trusted, its request id (`CF-Ray`, Akamai's `X-Akamai-Request-ID`, or a generic `X-Request-ID`) goes into the logs, onto error pages ("quote this reference"), and into the metadata of the action-log entries that request writes. | Lets support match an edge block or a 5xx to the request behind it. |
| W3 | **Long requests become jobs.** Replay, evidence export and action-log verification run in the background, like bulk imports already do: the request answers `202` at once with a run to poll (API), or a page that refreshes until the result is ready (web). The Controls page uses the last verification result, with how old it is, instead of walking the whole log on each load; verification runs daily and on demand. | Changes the replay and evidence export API responses, but only additively: the same endpoints may now answer `202` with a run. That needs SDK support and a changelog note; synchronous answers stay for small requests below a threshold. |
| W3 | **Connections and caching.** <br>• uvicorn's keep-alive timeout becomes configurable (`OPENCLEAR_KEEPALIVE_SECONDS`), defaulting to longer than the edges' origin idle timeouts (the guide lists them).<br>• `/static` gets `Cache-Control: public, max-age=31536000, immutable` when the `?v=` version matches the file, and a short cache otherwise.<br>• `no-store` stays on `/ui` and `/api`; a test checks that every route outside `/static` and the public branding images sends it.<br>• A `413` for a body over the limit gets a JSON body for API clients. | No compression in the app: the edge does Brotli or gzip. Compressing HTML that carries a CSRF token alongside reflected input invites BREACH-style attacks, so the guide notes the edge's settings. |
| W4 | **The edge check.** `scripts/edge_check.py https://openclear.bank.example` (and `openclear edge-check` in the SDK CLI) runs against a deployment and reports, with what it saw:<br>• the origin refuses direct requests (given the origin's address);<br>• the client IP OpenClear sees is the caller's;<br>• the CSP, HSTS and frame headers arrive unaltered, and nothing injects scripts;<br>• `/ui` and `/api` responses are never cached (`cf-cache-status`, `X-Cache`), and `/static` is cached;<br>• compression is on;<br>• a corpus of legitimate requests isn't blocked (an X9.37 and a NACHA upload, a SAML-sized POST, SCIM filters and PATCHes, a webhook URL, markdown with HTML-like text, a burst of API writes), each against the paths the guide excludes from WAF rules;<br>• a long request survives the edge's timeout, or comes back as a job (W3). | Uses a sandbox bank or a test bank's API client; it changes nothing outside that bank. Usable after go-live and after each WAF rule change. |
| W5 | **The deployment guide** (`docs/guides/edge.md`, on the docs site), with tested configurations for Cloudflare and Akamai and shorter notes for CloudFront with AWS WAF, Azure Front Door and F5. It covers:<br>• **Topology:** TLS at the edge, re-encrypted to the origin, plus origin lockdown (W2).<br>• **OpenClear settings:** `PUBLIC_BASE_URL`, `WEBAUTHN_ORIGIN`, client IP (W1), the edge secret.<br>• **WAF exclusions, path by path:** `/api/v1/bulk-uploads`, `/api/v1/mrm/replay`, `/ui/bulk-uploads`, `/ui/auth/saml/*`, `/scim/v2/*`, the webhook, SSO and embedding settings pages, `/ui/markdown-preview`. Each excludes named rules, never the whole WAF. Rules start in log-only mode for two weeks.<br>• **Bot management:** skip it for `/api/*`, `/scim/v2/*`, `/ui/auth/saml/*`, the OIDC back-channel logout, and `/health`. Optionally also skip it for API clients presenting an edge mTLS certificate.<br>• **Features to turn off:** Rocket Loader, Email Obfuscation, Zaraz and RUM injection; the "Add security headers" transform (OpenClear sends its own); Auto Minify where it's still offered.<br>• **Caching:** respect origin headers; never "cache everything" on `/ui` or `/api`.<br>• **Sizes and timeouts** per plan, and turning off edge retries for POST.<br>• **Edge rate limits** for sign-in and token endpoints.<br>• **Logs:** keep the edge's request id.<br>• **A go-live checklist** that ends with the edge check passing. | The README's proxy section shrinks to a pointer. `docs/compliance/ffiec.md` gains a WAF row. |
| W6 | **Optional: mTLS for API clients at the edge.** Cloudflare API Shield and Akamai can require a client certificate from the bank's core and pass its fingerprint on in a header. An API client in OpenClear can then be pinned to a certificate fingerprint (trusted only from the edge, per W1), so a leaked client secret alone isn't enough. | Only if a bank asks. It's additive: clients without a pin work as today. |

## Waves

The phases are built in **three waves**, grouped by the code they touch and by what's needed
to test them. **Each wave is one merge to `main`:** one run of the three full suites (SQLite,
Postgres, Linux), and one manual check. Within a wave, commits run only the tests they touch.
MASTER_PLAN.md places these waves among the UX and SIEM waves.

| Wave | Phases | Contents | Tested by |
|---|---|---|---|
| **A. Request handling** | W1, W2, W3 (connections and caching) | • **Request handling:** one middleware that works out each request's context once:<br>&nbsp;&nbsp;– the client IP (from a trusted header or the hop count);<br>&nbsp;&nbsp;– the request id (the edge's, or a new one);<br>&nbsp;&nbsp;– the person or API client.<br>&nbsp;&nbsp;Everything else reads that context.<br>• **Its users:**<br>&nbsp;&nbsp;– rate limits per person or API client;<br>&nbsp;&nbsp;– the edge secret;<br>&nbsp;&nbsp;– the source IP and request id in action-log metadata;<br>&nbsp;&nbsp;– request ids on error pages.<br>• **The "Edge in front" control.**<br>• **Server settings:**<br>&nbsp;&nbsp;– uvicorn's proxy trust and keep-alive;<br>&nbsp;&nbsp;– cache headers on static files;<br>&nbsp;&nbsp;– a JSON body on a 413.<br>• **The sandbox's** `--forwarded-allow-ips='*'` is replaced. | Unit and integration tests with simulated Cloudflare and Akamai headers and peer addresses. A manual check on the Fly demo and sandbox (Fly's proxy is one hop). |
| **B. Long requests** | W3 (jobs) | Replay, evidence export and action-log verification become jobs with a run to poll. The Controls page shows the last verification and its age, and verification also runs daily. The SDKs follow the `202` answers. | Integration tests for each job and its polling. A manual pass on Model risk, Controls and the export, using the shared busy and polling components from UX wave 0. |
| **C. Real edges** | W0, W4, W5 (W6 if asked); first against a **stub edge**, see below | The edge check script; the deployment guide; the WAF exclusions and bot-management bypasses, confirmed against a real Cloudflare zone (and Akamai if available); fixes for anything the zone shows. | The edge check passing against the sandbox behind Cloudflare. |

**A stub edge first.** A real zone needs working DNS and a certificate for a test hostname,
which can take a while to get right. So wave C builds and tests the edge check against a
local stand-in: a proxy container in front of OpenClear that behaves like each edge, adding:
- the edge's client-IP header (`CF-Connecting-IP`, or `True-Client-IP`);
- its request id (`CF-Ray`, or `X-Akamai-Request-ID`);
- the edge secret;
- a cut-off for slow origins (100 seconds, or 120);
- body-size limits, and caching by file extension;
- optionally, the header rewrites real edges make (Cloudflare's "Add security headers").

It runs in the test suite and in CI. The run against a real Cloudflare zone, and Akamai if
available, then confirms the stub's behaviour and the guide's settings; it isn't needed to
build anything.

**W0 isn't a separate step.** Waves A and B are safe whatever a real zone shows: every new
setting defaults to today's behaviour. So the real-zone work (W0) is done once, in wave C,
with the edge check that W4 builds. If a test domain is available earlier, a short
look-and-see session (no code) can run alongside wave A, and anything surprising is recorded
here.

## Decisions

| # | Question | Decision |
|---|---|---|
| W-D1 | Bundle Cloudflare's IP ranges in the code? | No. They change, and a stale bundled list fails quietly. A script prints the current ranges for configuration, and the Controls page warns when edge headers come from outside the configured ranges. |
| W-D2 | Trust the client-IP header from anywhere when it's configured? | No. The header is trusted only from `TRUSTED_PROXY_CIDRS`. A header from any other address is ignored and noted on the Controls page, since it suggests the origin is being reached around the edge. |
| W-D3 | Compression in the app? | No. It's left to the edge (and to a reverse proxy without one), because of the BREACH risk on pages with CSRF tokens. |
| W-D4 | Turn long requests into jobs (W3), or ask operators to raise edge timeouts? | Jobs. Raising Cloudflare's timeout needs Enterprise, and long-held requests also tie up workers. |
| W-D5 | Origin lockdown in the app, or at the network? | At the network (tunnel, mTLS, firewall). The shared-secret header is an extra layer the app can check and report on. |
| W-D6 | Support a specific provider's APIs (e.g. pushing WAF rules through Cloudflare's API)? | Not now. A guide with tested settings, plus the edge check, works for every provider without holding credentials to the bank's edge account. Possibly later, as a plugin. |
| W-D7 | Confirm on a real zone first (W0), or build first? | Build waves A and B first. Their settings default to today's behaviour, and the real-zone check happens once, in wave C, with the edge check script. |

## Testing

- **Unit and integration (in the suite):**
  - client-IP resolution for each combination of peer address, header and configured ranges, including spoofed headers from outside the ranges;
  - the edge secret, including rotation and `/health`;
  - per-principal rate limits;
  - the cache headers;
  - jobs for replay, export and verification;
  - the Controls edge control, with simulated Cloudflare and Akamai headers.
- **The edge check against real zones** (W0, then each release): the sandbox behind a
  Cloudflare zone; an Akamai property when available.
- **WAF corpus:** the legitimate requests the edge check sends are also kept as fixtures,
  so a change in OpenClear that would newly look like an attack (a new field holding a
  URL, say) shows up in review.

## Progress log

(Newest last.)
- 2026-10-09 Plan written from a review of the code: client IP, rate limits, headers,
  caching, server settings, long requests, and how they meet Cloudflare and Akamai.
  Nothing built yet. W0 needs a Cloudflare zone on a domain we control, and, ideally,
  Akamai access.
- 2026-10-10 Reorganised into three waves (A request handling, B long requests, C real edges)
  to cut test cycles; W0 moves into wave C. See MASTER_PLAN.md for the overall order.
- 2026-10-10 Wave A built (M1): `web/request_context.py`, per-principal rate limits, the edge secret, request ids, source IP in the action log, the "Edge network set up" control, keep-alive, static cache headers, JSON 413, the sandbox's proxy setting.
- 2026-10-10 Wave B built (M2): `services/long_runs.py` (replay over 1 MB, evidence export over 32 days, action-log verification as `control_run` jobs), status and download endpoints, daily verification, Controls reading the last verification, the CLI waiting for a queued export.
- 2026-10-10 A real Cloudflare zone is parked: DNS for a test hostname proved fiddly, and nothing before wave C needs it. Wave C builds against a stub edge (a local proxy imitating Cloudflare and Akamai); the real-zone run becomes a final confirmation. The test certificate added on the sandbox was removed.
- 2026-10-11 Error pages ahead of M3: branded pages for 403 (expired form), 404, 405, 413, 422 and 429 on web paths, JSON on the API, 503 with Retry-After for a database outage, `/health` checking the database, and self-contained 502/503/504 pages in `static/errors/`. The wave C guide says how to wire those into Cloudflare, Akamai and nginx.
- 2026-10-12 Wave C built (M7) against the stub edge: `src/openclear/edge_check.py` and `scripts/edge_check.py` (W4), `GET /api/v1/edge/echo`, `docs/guides/edge.md` (W5), `scripts/edge_ip_ranges.py` (from W1, not built until now; Cloudflare's website refuses scripted fetches, so it reads Cloudflare's public IP API). `tests/stub_edge.py` imitates Cloudflare and Akamai (client-IP header, request id, edge secret, static caching, gzip, body limit, origin timeout) with faults for a missing IP header, injected scripts, added security headers, cache-everything, WAF blocks on markup and SAML, and an API rate limit; the suite shows a sound setup passing and each fault caught. W0 (a real Cloudflare zone, Akamai if available) remains, as confirmation.
- 2026-10-12 First run against a real proxy (the sandbox on Fly): everything applicable passed after one real finding, the caller's address (Fly appends its own hop to X-Forwarded-For; the sandbox now reads Fly-Client-IP). Fly added to the guide and the edge hints. A Cloudflare zone is still the confirmation W0 asks for.

