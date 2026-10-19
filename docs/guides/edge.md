# Running OpenClear behind an edge network

Most banks put a web application behind an edge network: Cloudflare, Akamai, AWS CloudFront
with AWS WAF, Azure Front Door, or an F5. It terminates TLS, absorbs attacks, and runs a WAF
and bot management. This guide is for whoever sets that up. It covers what OpenClear needs from
the edge, the WAF and bot exceptions it needs, and how to check the result.

The short version:

1. Send everything through the edge, and lock the origin down so nothing gets around it.
2. Tell OpenClear which header carries the caller's address.
3. Exclude a short list of paths from a few named WAF rules, never the whole WAF.
4. Skip bot management for machine-to-machine paths.
5. Turn off the edge features that rewrite pages.
6. Run the edge check, `scripts/edge_check.py`, and fix whatever it reports.

## 1. Topology and origin lockdown

TLS ends at the edge and is re-encrypted to the origin (Cloudflare "Full (strict)", Akamai
"Origin certificate verification"). Then make sure only the edge can reach the origin. In
order of preference:

1. **A tunnel**, so the origin has no inbound port at all (Cloudflare Tunnel).
2. **Mutual TLS from the edge**, checked at your load balancer (Cloudflare Authenticated Origin
   Pulls; Akamai origin mTLS).
3. **The edge's address ranges** allowed at the firewall, and nothing else (Cloudflare's
   published ranges; Akamai Site Shield).

On top of any of these, have the edge add a secret header, and OpenClear refuses requests
without it (apart from `/health`):

```sh
OPENCLEAR_EDGE_SECRET=<a long random value>
OPENCLEAR_EDGE_SECRET_HEADER=X-OpenClear-Edge-Secret   # the default
# while rotating, the old one too:
OPENCLEAR_EDGE_SECRET_PREVIOUS=<the old value>
```

Cloudflare: a Transform Rule ("Modify Request Header", set `X-OpenClear-Edge-Secret`). Akamai:
the "Modify Outgoing Request Header" behaviour. If you lock down with a tunnel or mTLS and
don't use the header, say how in `OPENCLEAR_EDGE_ATTESTATION` (for example "Cloudflare Tunnel
only; no public listener"), so the Controls page and the evidence export can show it.

## 2. OpenClear's settings

| Setting | Value |
|---|---|
| `OPENCLEAR_PUBLIC_BASE_URL` | The address people use, through the edge (`https://openclear.bank.example`). |
| `OPENCLEAR_WEBAUTHN_RP_ID`, `OPENCLEAR_WEBAUTHN_ORIGIN` | That host name and origin, so security keys work. |
| `OPENCLEAR_CLIENT_IP_HEADER` | `CF-Connecting-IP` (Cloudflare) or `True-Client-IP` (Akamai). |
| `OPENCLEAR_TRUSTED_PROXY_CIDRS` | The edge's ranges, comma-separated. For Cloudflare, `python scripts/edge_ip_ranges.py cloudflare` prints them. For Akamai, copy your Site Shield map's ranges. The header is trusted only on connections from these. |
| `OPENCLEAR_KEEPALIVE_SECONDS` | Longer than the edge's idle timeout to the origin (Cloudflare and Akamai both keep connections for minutes; 75 or more is safe). |
| `OPENCLEAR_EDGE_SECRET` or `OPENCLEAR_EDGE_ATTESTATION` | See section 1. |

With these set, **Settings → Controls → "Edge network set up"** passes. It fails when edge
headers arrive but OpenClear isn't set up to read them, and warns without a lockdown.
`GET /api/v1/edge/echo` shows how OpenClear saw your own request: your address, where it came
from, and whether the edge was trusted.

Every response carries `X-Request-ID`. Behind a trusted edge it's the edge's own id (`CF-Ray`,
`X-Akamai-Request-ID`), so an edge log line, an OpenClear log line, an error page and an
action-log entry can be matched up.

## 3. WAF exceptions

Positive pay traffic looks unusual to generic WAF rules: binary check files, ACH files full of
digits, large signed SAML responses, SCIM filter syntax, and settings that legitimately contain
URLs or HTML-like text. Exclude these paths from the **specific rules that fire**, never from
the WAF as a whole. Start every new rule in log-only mode for two weeks, then enforce it.

| Path | What's in it | Rules that tend to fire |
|---|---|---|
| `/api/v1/bulk-uploads`, `/ui/*/bulk`, `/ui/bulk-uploads/*`, `/ui/layouts`, `/ui/issued-items/bulk` | X9.37 (binary, EBCDIC), NACHA (fixed-width digits), CSV and Excel | Binary or multipart body inspection, "file upload" and SQL-injection signatures on digit runs |
| `/api/v1/mrm/replay`, `/ui/admin/model-risk/*` | A past X9.37 or NACHA file | The same |
| `/ui/check-images/*`, `/api/v1/check-images` | Check images | Image and multipart rules |
| `/ui/sso/saml/*/acs` | A base64 SAML response, often 10–30 KB, with XML signatures | XML, XXE and "large parameter" rules; body size limits |
| `/ui/sso/oidc/*/backchannel-logout` | A signed logout token | JWT and long-parameter rules |
| `/scim/v2/*` | SCIM filters (`userName eq "a@b"`) and JSON PATCH | SQL-injection and protocol rules |
| `/ui/markdown-preview`, `/ui/settings/messages`, `/ui/customers/*/banner` | Markdown that may contain HTML-like text and small base64 images | XSS signatures |
| `/ui/admin/webhooks*`, `/ui/admin/sso*`, `/ui/settings/embedding`, `/ui/settings/security-events` | URLs, OIDC issuers, certificates | SSRF and "URL in parameter" rules |
| `/api/v1/oauth/token`, `/api/v1/embed/launch` | Client secrets and tokens in bodies | Credential-stuffing heuristics (rate-limit these instead; see section 7) |

**Cloudflare:** in the WAF, add an exception (a "Skip" rule) on the Cloudflare Managed Ruleset and
the OWASP Core Ruleset, matching these paths, and skipping only the rule IDs your log-only
period showed firing. Keep "File upload" and malware scanning on if you use them; they read the
file, not as text.

**Akamai (App & API Protector):** add rule exceptions on the affected rules, scoped to these
paths (and, where the product allows, to the parameter: `SAMLResponse`, `file`, `text`). Raise
the request body inspection limit if SAML responses are cut short.

## 4. Bot management

Machine-to-machine traffic has no browser to solve a challenge. Skip bot management (and any
JavaScript or CAPTCHA challenge) for:

- `/api/*`: cores, online banking servers, SDKs;
- `/scim/v2/*`: identity providers;
- `/ui/sso/saml/*` and `/ui/sso/oidc/*`: identity providers' posts and back-channel calls;
- `/health`: load balancer and uptime checks.

Optionally, require an mTLS client certificate from API clients at the edge (Cloudflare API
Shield, Akamai mTLS) and skip bot management for those requests only.

Cloudflare: Super Bot Fight Mode (Pro and above) can skip these paths with a WAF custom rule
("Skip: Super Bot Fight Mode"). The free plan's Bot Fight Mode can't be scoped; turn it off if
API clients are challenged. Akamai Bot Manager: exclude these paths in its match targets.

## 5. Edge features to turn off

OpenClear sends a strict Content-Security-Policy, so anything the edge injects is blocked in the
browser anyway, and rewrites can break pages. For this site, turn off:

- Cloudflare: **Rocket Loader**, **Email Address Obfuscation**, **Zaraz**, automatic **Web
  Analytics** (RUM) injection, and the managed transform **"Add security headers"** (OpenClear
  sends its own, stricter ones). Auto Minify where it's still offered.
- Akamai: **mPulse** (RUM) injection, and any "add security headers" or HTML rewriting.

Don't compress HTML at the origin; let the edge do Brotli or gzip. (OpenClear doesn't compress
pages itself: compressing a page that holds a CSRF token next to reflected input invites
BREACH-style attacks, and edges handle it better.)

## 6. Caching

- Respect the origin's `Cache-Control`. OpenClear sends `no-store` on every page and API
  response, and a year, immutable, on versioned static files (`/static/...?v=`).
- Never "cache everything" on `/ui` or `/api`. A cached page can show one person's data to
  another.
- Static files can be cached by extension (the default on both edges).

## 7. Sizes, timeouts, retries and rate limits

- **Upload size.** Cloudflare caps request bodies by plan (100 MB on Free and Pro, more on
  Business and Enterprise); Akamai by configuration. Keep the edge's limit at or above
  `OPENCLEAR_MAX_REQUEST_BODY_BYTES`. Larger check-image archives should go through the API in
  parts or through the drop folder.
- **Timeouts.** Edges give up on a slow origin after about 100 seconds (Cloudflare) or 120
  (Akamai). OpenClear runs anything long as a background job (replays of large files, long
  evidence exports, verifying the action log), so this is enough.
- **Retries.** Turn off the edge's automatic retry of `POST` requests to the origin: a
  retried upload or decision is a duplicate.
- **Rate limits.** Add edge rate limits on the endpoints that don't sign anyone in:
  `/ui/login`, `/ui/platform/login`, `/api/v1/auth/login`, `/api/v1/oauth/token`,
  `/api/v1/embed/launch`, and SCIM authentication. OpenClear's own limits stay as the
  backstop. Don't rate-limit authenticated API traffic by address at the edge: a core
  system's calls all come from one address.

## 8. Error pages and logs

- Point the edge's origin-error pages at OpenClear's own: `static/errors/502.html`,
  `503.html` and `504.html` are self-contained (no external files). Cloudflare: Custom Error
  Pages, or a Worker. Akamai: "Site Failover" or "Custom error responses".
- Keep the edge's request id in its logs (`CF-Ray`, `X-Akamai-Request-ID`): OpenClear records
  the same id.
- If the bank's SIEM takes edge logs, the same request id ties them to OpenClear's security
  events (see the [SIEM guide](siem.md)).
- **Log paths without their query strings, or with them masked.** OpenClear keeps account
  numbers out of URLs (searches are posted; list filters carry account ids), but a link
  someone makes by hand, or an old bookmark, can still carry a search term. Cloudflare: Logpush
  without `ClientRequestURI` (use `ClientRequestPath`), or a Logpush filter that drops the
  query. Akamai: DataStream's "Query string" field off. OpenClear's own access log records
  only the path.

## 9. The edge check

```sh
python scripts/edge_check.py https://openclear.bank.example \
    --origin https://origin.internal.example:8443 --expect-ip <this machine's public address>
```

It needs no account and changes nothing: every request it makes is one OpenClear refuses, so
what matters is that it got through the edge. It checks that:

- OpenClear is reachable through the edge, and sees the caller's real address;
- the origin refuses requests that go around the edge;
- the security headers arrive as OpenClear sent them, and nothing is injected;
- pages and the API aren't cached, static files are, and responses are compressed;
- requests your people and systems really send get through: X9.37 and NACHA uploads, a large
  SAML response, an OIDC back-channel sign-out, SCIM searches and changes, markdown with
  HTML-like text, URLs in settings, and a burst of API calls.

Each check is PASS, WARN, FAIL or SKIP with what it saw (`--json` for a machine-readable
report); the script exits 1 if anything fails. Run it at go-live and after every change to the
edge's WAF, bot or caching rules.

## Go-live checklist

- [ ] TLS at the edge, re-encrypted and verified to the origin.
- [ ] The origin locked down (tunnel, mTLS or address ranges), plus the edge secret header or an attestation.
- [ ] `OPENCLEAR_PUBLIC_BASE_URL`, WebAuthn settings, `OPENCLEAR_CLIENT_IP_HEADER` and `OPENCLEAR_TRUSTED_PROXY_CIDRS` set.
- [ ] WAF exceptions for the paths in section 3, scoped to named rules, after two weeks in log-only mode.
- [ ] Bot management skipped for `/api/*`, `/scim/v2/*`, the SSO paths and `/health`.
- [ ] Rocket Loader, email obfuscation, Zaraz, RUM injection and "Add security headers" off.
- [ ] No "cache everything" on `/ui` or `/api`; POST retries off; edge rate limits on sign-in and token endpoints.
- [ ] Origin error pages pointing at `static/errors/`.
- [ ] Edge logs record paths without query strings.
- [ ] Settings → Controls → "Edge network set up" passes.
- [ ] `scripts/edge_check.py` passes.

## Other edges, briefly

- **AWS CloudFront with AWS WAF:** use `CloudFront-Viewer-Address` as the client-IP header (it
  carries the port too; strip it with a CloudFront Function into `X-Client-IP` and use that),
  and AWS's published CloudFront ranges. The AWS Managed Rules' `SizeRestrictions_BODY` and
  `CrossSiteScripting_BODY` rules need exceptions on the upload and markdown paths.
- **Azure Front Door:** `X-Azure-ClientIP` as the client-IP header, the `AzureFrontDoor.Backend`
  service tag at the origin, and `X-Azure-FDID` checked by your load balancer (or the edge
  secret). The Default Rule Set needs the same path exceptions.
- **Fly.io:** Fly's proxy sets `Fly-Client-IP` (overwriting anything a client sends) and appends
  its own address to `X-Forwarded-For`, so the last hop there is Fly's, not the caller's. Use
  `OPENCLEAR_CLIENT_IP_HEADER=Fly-Client-IP` with `OPENCLEAR_TRUSTED_PROXY_CIDRS=172.16.0.0/12,fdaa::/16`
  (Fly's private network, which is the only way into a machine). The hosted sandbox runs this way.
- **F5 (BIG-IP ASM / Advanced WAF):** insert `X-Forwarded-For` and use
  `OPENCLEAR_TRUSTED_PROXY_COUNT=1`, or a custom header with `OPENCLEAR_CLIENT_IP_HEADER`. Add the
  upload and SAML paths as file-upload and XML profiles rather than turning signatures off.

The behaviour described here has been checked against a stand-in edge in OpenClear's own tests
(`tests/stub_edge.py`, imitating Cloudflare and Akamai); confirm it on your own zone with the edge
check.
