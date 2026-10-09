# Embedding OpenClear in online banking

A bank's business customers can review and decide their positive pay exceptions without leaving the bank's online banking. This guide is for the online banking platform's developers and the bank's OpenClear admin.

## How it fits together

1. **The person signs in to online banking**, as they do today.
2. **Online banking's server** asks OpenClear for a one-time launch URL for that person. It does this in one of two ways:
   - **Token exchange (recommended).** The bank sets up an OIDC sign-on connection in OpenClear with online banking as the identity provider and a *customer claim*. Online banking's server then trades the person's ID token for an OpenClear token. The person is created in OpenClear the first time, with the security group their roles map to, scoped to their own business customer.
   - **Acting as the person.** An existing OpenClear user is named with `OpenClear-Act-As` (their email) or `OpenClear-Act-As-Subject` (online banking's own id for them).
3. **Online banking's page** shows the launch URL with `@openclear/embed`, or in a WebView in a mobile app. The URL starts an embedded session:
   - it runs as that person, with exactly what both they and the API client may do;
   - it has no navigation;
   - it can be framed only by the bank's allowed origins.

```
online banking server ──POST /api/v1/oauth/token (token exchange)──▶ OpenClear
                      ──POST /api/v1/embed/launch {parent_origin, path}──▶ { url } (one minute, once)
online banking page   ── mount(container, { url }) ──▶ <iframe> embedded session
```

## For the bank's OpenClear admin

1. **Admin → Single sign-on:** add an OIDC connection for your online banking platform.
   - Set its *customer claim*, for example `customer_number`. The claim must carry the OpenClear customer number of each person's business.
   - Map its roles to security groups, for example `obp-approvers` → Approver and `obp-viewers` → Viewer.
2. **Admin → API clients:** create a client for the platform's server.
   - Choose **May act for your users**.
   - Link the client to that connection.
   - Its security group caps what any person can do through it.
3. **Settings → Embedding:** list your online banking's https origins.

## For the online banking developer

With the Python SDK:

```python
from openclear_sdk import OpenClear

oc = OpenClear("https://openclear.yourbank.example", client_id="occ_...", client_secret="...")
person = oc.for_person(id_token)                       # the signed-in person's ID token
launch = person.embed.create_launch({"parent_origin": "https://banking.yourbank.example", "path": "/ui/exceptions"})
# hand launch["url"] to your page
```

The TypeScript SDK has the same calls: `oc.forPerson(idToken)`, then `embed.createLaunch(...)`.

On the page:

```ts
import { mount } from "@openclear/embed";
const openclear = mount(container, { url, onSessionEnded: async () => openclear.navigate(await newLaunchUrl()) });
```

**Events** are `navigated`, `exception-decided`, `exception-recommended` and `session-ended`. Use them to update your page, for example a toast or a badge count. For anything your systems must act on, use webhooks or the decision feed.

## Mobile apps (Mifos Mobile and others)

A native app opens the launch URL in a WebView. The WebView shows OpenClear as a top-level page, so framing rules don't apply and its cookies are first-party.

1. Get the launch URL from your app's backend, just as a web page would.
2. Load it in the WebView.
3. Watch for navigation to `/ui/embed/ended`. That means the session ended, so get a new launch URL.

For Mifos (Apache Fineract), the OpenClear connection's identity provider is the one Mifos self-service users sign in with, typically Keycloak. Map the customer claim to the Fineract client's external id, and use that as the OpenClear customer number when the core syncs customers (`PUT /api/v1/customers/{customer_number}`).

## Cookies and hosting

When OpenClear is framed by a different site, its session cookies are `SameSite=None; Secure; Partitioned`. That keeps them in a jar for that one online banking site, which browsers that limit third-party cookies still allow.

Hosting OpenClear on a subdomain of the online banking domain avoids the question altogether. For example, `positivepay.yourbank.example` beside `banking.yourbank.example`.

## Security notes

- Ordinary OpenClear sessions can never be framed: `frame-ancestors 'none'` and `X-Frame-Options: DENY`.
- An embedded session's pages may be framed only by the origin the session was launched for, and only while the bank still lists it.
- A launch URL is good for 60 seconds and works once.
- When a session ends, the frame shows a page that tells its parent. It carries no data, and it's the only page any site may frame.
