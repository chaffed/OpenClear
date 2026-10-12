# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The edge check (WAF_PLAN.md W4): run against a deployment through its edge network
(Cloudflare, Akamai, a load balancer) to see that the edge and OpenClear fit together.

- OpenClear is reachable, and sees the caller's real address, read from the edge;
- the origin refuses requests that go around the edge (given the origin's address);
- the security headers arrive unaltered, and the edge injects no scripts;
- pages and the API are never cached by the edge; static files are; responses are compressed;
- requests a bank's people and systems really send aren't blocked by the edge's WAF or bot
  management: file uploads, SAML and SCIM, webhook and markdown settings, a burst of API calls.

It needs no account and changes nothing: every request it makes is one OpenClear refuses
(not signed in), so what matters is only that it got through the edge to OpenClear. A
response from OpenClear always carries X-Request-ID; one stopped by the edge doesn't.

    python scripts/edge_check.py https://openclear.bank.example --origin https://origin.internal:8443
"""

import base64
import json
import re
import uuid
from dataclasses import asdict, dataclass, field

import httpx

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
# What an edge injects into pages, by the scripts' paths and names.
INJECTED = ("/cdn-cgi/", "rocket-loader", "email-decode", "zaraz", "cloudflareinsights", "beacon.min.js", "/akam/", "boomerang",
            "mpulse", "go-mpulse", "rum-")
EDGE_CACHE_HEADERS = ("cf-cache-status", "x-cache", "x-cache-status", "x-akamai-cache-status")
ORIGIN_MARK = "x-request-id"


@dataclass
class Check:
    key: str
    title: str
    status: str
    detail: str
    evidence: dict = field(default_factory=dict)


def _cache_hit(response: httpx.Response) -> bool:
    for name in EDGE_CACHE_HEADERS:
        value = response.headers.get(name, "").upper()
        if "HIT" in value:
            return True
    return False


def _from_origin(response: httpx.Response) -> bool:
    return ORIGIN_MARK in response.headers


def _edge_name(response: httpx.Response) -> str | None:
    headers = response.headers
    if "cf-ray" in headers or headers.get("server", "").lower() == "cloudflare":
        return "Cloudflare"
    if any(k.startswith("x-akamai") for k in headers) or "akamai" in headers.get("server", "").lower():
        return "Akamai"
    if "via" in headers:
        return f"a proxy ({headers['via']})"
    return None


# --- Probes: legitimate requests a WAF or bot management might block --------------------------

_NACHA = ("101 091000019 0910000191610150101A094101FIRST BANK             ACME CORP              \n"
          "5225ACME CORP                           1234567890PPDPAYROLL         261015   1091000010000001\n"
          "62209100001912345678901      0000012345ID-1           JOHN DOE                0091000010000001\n"
          "822500000100091000010000000123450000000000001234567890                         091000010000001\n"
          "9000001000001000000010009100001000000012345000000000000                                       \n").encode()
_X937 = b"\x00\x00\x00\x50" + "0103T091000019091000019202610151200NACME BANK         FIRST BANK         ".encode("cp500")
_SAML = base64.b64encode((
    '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="_x" Version="2.0">'
    '<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"><ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
    + "<ds:SignatureValue>" + "A" * 16000 + "</ds:SignatureValue></ds:Signature>"
    "<saml:Subject><saml:NameID>alice@bank.example</saml:NameID></saml:Subject></saml:Assertion></samlp:Response>").encode()).decode()
_MARKDOWN = ("**Notice:** checks over $5,000 need a call-back. Don't paste <script>alert('x')</script> or "
             "SELECT * FROM accounts WHERE 1=1; -- examples into messages. <img src=x onerror=alert(1)> [link](https://bank.example/?a=1&b=2)")


def _probes() -> list[tuple[str, str, str, dict]]:
    """(name, method, path, httpx request kwargs)."""
    a, b = uuid.uuid4(), uuid.uuid4()
    return [
        ("NACHA file upload", "POST", "/api/v1/bulk-uploads",
         {"data": {"kind": "ach_transactions", "format": "nacha"}, "files": {"file": ("ach.txt", _NACHA, "text/plain")}}),
        ("X9.37 file upload", "POST", "/api/v1/bulk-uploads",
         {"data": {"kind": "check_images", "format": "x937"}, "files": {"file": ("letter.x937", _X937, "application/octet-stream")}}),
        ("Replay of a past file", "POST", "/api/v1/mrm/replay", {"files": {"file": ("letter.x937", _X937, "application/octet-stream")}}),
        ("Upload from the web", "POST", "/ui/issued-items/bulk", {"files": {"upload_file": ("items.csv", b"account_number,check_number\n1,2\n", "text/csv")}}),
        ("SAML sign-in (a large signed response)", "POST", f"/ui/sso/saml/{a}/{b}/acs", {"data": {"SAMLResponse": _SAML, "RelayState": "/ui/"}}),
        ("OIDC back-channel sign-out", "POST", f"/ui/sso/oidc/{a}/{b}/backchannel-logout", {"data": {"logout_token": "eyJ" + "a" * 400 + ".eyJ" + "b" * 400 + ".sig"}}),
        ("SCIM search", "GET", '/scim/v2/Users?filter=userName eq "alice@bank.example" and active eq true', {}),
        ("SCIM change", "PATCH", f"/scim/v2/Users/{a}", {"json": {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                                                                 "Operations": [{"op": "replace", "path": "active", "value": False}]}}),
        ("Markdown with HTML-like text", "POST", "/ui/markdown-preview", {"data": {"text": _MARKDOWN}}),
        ("A webhook URL in a setting", "POST", "/ui/admin/webhooks", {"data": {"url": "https://hooks.bank.example/openclear?token=abc&x=1"}}),
        ("Embedding origins in a setting", "POST", "/ui/settings/embedding", {"data": {"embed_allowed_origins": "https://banking.bank.example\nhttps://m.bank.example"}}),
    ]


# --- The checks ---------------------------------------------------------------------------------


def run(base_url: str, *, origin_url: str | None = None, expect_ip: str | None = None, timeout: float = 30.0,
        burst: int = 20, verify: bool = True) -> list[Check]:
    base_url = base_url.rstrip("/")
    checks: list[Check] = []
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False, verify=verify,
                      headers={"user-agent": "OpenClear-EdgeCheck/1", "accept-encoding": "gzip, br"}) as client:
        try:
            health = client.get("/health")
        except httpx.HTTPError as exc:
            return [Check("reachable", "Reachable through the edge", FAIL, f"Couldn't reach {base_url}: {exc}")]
        if health.status_code != 200:
            return [Check("reachable", "Reachable through the edge", FAIL, f"/health answered {health.status_code}.",
                          {"status": health.status_code, "from_openclear": _from_origin(health)})]
        edge = _edge_name(health)
        checks.append(Check("reachable", "Reachable through the edge", PASS,
                            f"/health is ok{f', through {edge}' if edge else ''}.", {"edge": edge}))

        checks += _client_ip(client, expect_ip)
        checks.append(_origin_lockdown(origin_url, timeout, verify))
        login = client.get("/ui/login")
        origin_login = None
        if origin_url:
            try:
                origin_login = httpx.get(origin_url.rstrip("/") + "/ui/login", timeout=timeout, verify=verify)
            except httpx.HTTPError:
                origin_login = None
        checks.append(_headers(login, origin_login, base_url))
        checks.append(_injection(login))
        checks.append(_not_cached(client))
        checks.append(_static_cached(client, login))
        checks.append(_compression(login))
        checks.append(_not_blocked(client))
        checks.append(_burst(client, burst))
    return checks


def _client_ip(client: httpx.Client, expect_ip: str | None) -> list[Check]:
    response = client.get("/api/v1/edge/echo")
    if response.status_code != 200:
        return [Check("client_ip", "The caller's address", FAIL, f"/api/v1/edge/echo answered {response.status_code}.")]
    seen = response.json()
    evidence = {"seen": seen}
    title = "The caller's address"
    if seen["edge_headers"] and not seen["edge_trusted"]:
        return [Check("client_ip", title, FAIL,
                      f"Edge headers arrive ({', '.join(seen['edge_headers'])}) but OpenClear doesn't trust them, so it sees "
                      f"{seen['client_ip']}, not the caller. Set OPENCLEAR_CLIENT_IP_HEADER and OPENCLEAR_TRUSTED_PROXY_CIDRS.", evidence)]
    if expect_ip and seen["client_ip"] != expect_ip:
        return [Check("client_ip", title, FAIL, f"OpenClear sees {seen['client_ip']}, not this machine's {expect_ip}.", evidence)]
    if expect_ip and seen["edge_trusted"] and seen["client_ip_from"] in ("header", "forwarded"):
        return [Check("client_ip", title, PASS, f"OpenClear sees {seen['client_ip']}, this machine's address, read from the "
                                                 f"{'edge' if seen['client_ip_from'] == 'header' else 'proxy'}'s header.", evidence)]
    if seen["client_ip_from"] == "connection" and seen["edge_headers"]:
        return [Check("client_ip", title, FAIL, "An edge is in front, but OpenClear reads the address from the connection.", evidence)]
    if not seen["edge_headers"]:
        return [Check("client_ip", title, WARN, f"No edge headers reached OpenClear; it sees {seen['client_ip']} from the "
                                                 f"{seen['client_ip_from']}. Is the edge in front?", evidence)]
    return [Check("client_ip", title, PASS, f"OpenClear sees {seen['client_ip']}, read from the edge's header.", evidence)]


def _origin_lockdown(origin_url: str | None, timeout: float, verify: bool) -> Check:
    title = "The origin refuses requests around the edge"
    if not origin_url:
        return Check("origin_lockdown", title, SKIP, "Give the origin's own address (--origin) to check this.")
    try:
        direct = httpx.get(origin_url.rstrip("/") + "/api/v1/edge/echo", timeout=min(timeout, 10), verify=verify)
    except httpx.HTTPError as exc:
        return Check("origin_lockdown", title, PASS, f"A direct connection doesn't get through ({type(exc).__name__}).")
    if direct.status_code == 200:
        return Check("origin_lockdown", title, FAIL,
                     "The origin answers requests that don't come through the edge. Use a tunnel, mutual TLS, the edge's "
                     "address ranges at the firewall, or OPENCLEAR_EDGE_SECRET.", {"status": 200})
    return Check("origin_lockdown", title, PASS, f"The origin refuses a direct request ({direct.status_code}).",
                 {"status": direct.status_code})


_REQUIRED_HEADERS = {"content-security-policy": None, "x-frame-options": "DENY", "x-content-type-options": "nosniff",
                     "referrer-policy": None}


def _without_nonce(csp: str) -> str:
    return re.sub(r"'nonce-[^']+'", "'nonce'", csp or "")


def _headers(login: httpx.Response, origin_login: httpx.Response | None, base_url: str) -> Check:
    title = "Security headers arrive unaltered"
    problems = []
    for name, value in _REQUIRED_HEADERS.items():
        got = login.headers.get(name)
        if got is None:
            problems.append(f"{name} is missing")
        elif value and got.strip().lower() != value.lower():
            problems.append(f"{name} is {got!r}, not {value!r}")
    if origin_login is not None:
        for name in _REQUIRED_HEADERS:
            mine, theirs = origin_login.headers.get(name), login.headers.get(name)
            if name == "content-security-policy":
                mine, theirs = _without_nonce(mine), _without_nonce(theirs)
            if mine and theirs and mine != theirs:
                problems.append(f"{name} differs from what OpenClear sent")
        if login.headers.get_list("content-security-policy") and len(login.headers.get_list("content-security-policy")) > 1:
            problems.append("a second content-security-policy was added")
    if base_url.startswith("https://") and "strict-transport-security" not in login.headers:
        return Check("headers", title, WARN if not problems else FAIL,
                     "; ".join(problems + ["no Strict-Transport-Security: set HSTS at the edge or with OPENCLEAR's proxy settings"]))
    if problems:
        return Check("headers", title, FAIL, "; ".join(problems) + ". Turn off the edge's header changes (e.g. Cloudflare's "
                                                                    "\"Add security headers\") for this site.")
    return Check("headers", title, PASS, "Content-Security-Policy, X-Frame-Options, X-Content-Type-Options and Referrer-Policy "
                                         "arrive as OpenClear sent them.")


def _injection(login: httpx.Response) -> Check:
    title = "No scripts injected by the edge"
    body = login.text.lower()
    found = sorted({marker for marker in INJECTED if marker in body})
    if found:
        return Check("injection", title, FAIL,
                     f"The page carries scripts the edge added ({', '.join(found)}). Turn off Rocket Loader, Email Obfuscation, "
                     "Zaraz and real-user monitoring for this site: OpenClear's Content-Security-Policy blocks them anyway.", {"found": found})
    return Check("injection", title, PASS, "The sign-in page carries only OpenClear's own scripts.")


def _not_cached(client: httpx.Client) -> Check:
    title = "Pages and the API aren't cached"
    problems = []
    for path in ("/ui/login", "/api/v1/edge/echo"):
        first, second = client.get(path), client.get(path)
        if "no-store" not in second.headers.get("cache-control", ""):
            problems.append(f"{path} lost its Cache-Control: no-store")
        if _cache_hit(second):
            problems.append(f"{path} was served from the edge's cache")
        if first.headers.get(ORIGIN_MARK) and first.headers.get(ORIGIN_MARK) == second.headers.get(ORIGIN_MARK):
            problems.append(f"{path} came back with the same request id twice (a cached copy)")
    if problems:
        return Check("not_cached", title, FAIL, "; ".join(problems) + ". Never \"cache everything\" on /ui or /api; respect "
                                                                      "the origin's Cache-Control.")
    return Check("not_cached", title, PASS, "Each request to /ui and /api reached OpenClear, with no-store intact.")


def _static_cached(client: httpx.Client, login: httpx.Response) -> Check:
    title = "Static files are cached"
    match = re.search(r'(/static/[^"\']+\?v=[^"\']+)', login.text)
    if not match:
        return Check("static_cached", title, SKIP, "No versioned static file on the sign-in page to try.")
    path = match.group(1)
    client.get(path)
    second = client.get(path)
    control = second.headers.get("cache-control", "")
    if "immutable" not in control:
        return Check("static_cached", title, WARN, f"{path} came back with Cache-Control {control!r}; OpenClear sends a year, "
                                                   "immutable. The edge may be overriding it.", {"cache_control": control})
    if any(name in second.headers for name in EDGE_CACHE_HEADERS) and not _cache_hit(second):
        return Check("static_cached", title, WARN, f"{path} wasn't served from the edge's cache on a second request.")
    return Check("static_cached", title, PASS, f"{path} is cacheable for a year{' and was served from the edge cache' if _cache_hit(second) else ''}.")


def _compression(login: httpx.Response) -> Check:
    title = "Responses are compressed"
    encoding = login.headers.get("content-encoding")
    if encoding:
        return Check("compression", title, PASS, f"Pages arrive {encoding}-encoded.")
    return Check("compression", title, WARN, "Pages arrive uncompressed; turn on Brotli or gzip at the edge (OpenClear doesn't "
                                             "compress HTML itself).")


def _not_blocked(client: httpx.Client) -> Check:
    title = "Legitimate requests get through"
    blocked, evidence = [], []
    for name, method, path, kwargs in _probes():
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            blocked.append(f"{name} ({type(exc).__name__})")
            continue
        reached = _from_origin(response)
        evidence.append({"probe": name, "path": path.split("?")[0], "status": response.status_code, "reached_openclear": reached})
        if not reached:
            blocked.append(f"{name}: {method} {path.split('?')[0]} stopped by the edge ({response.status_code})")
    if blocked:
        return Check("not_blocked", title, FAIL, "; ".join(blocked) + ". Exclude these paths from the named WAF and bot rules "
                                                                       "(docs/guides/edge.md).", {"probes": evidence})
    return Check("not_blocked", title, PASS, f"All {len(evidence)} requests reached OpenClear (and were refused there, as they "
                                             "carry no sign-in).", {"probes": evidence})


def _burst(client: httpx.Client, count: int) -> Check:
    title = "A burst of API calls isn't stopped by the edge"
    statuses = []
    for _ in range(count):
        response = client.post("/api/v1/issued-items", json={"check_number": "1"})
        statuses.append((response.status_code, _from_origin(response)))
    edge_stops = [s for s, from_origin in statuses if not from_origin]
    if edge_stops:
        return Check("burst", title, FAIL, f"{len(edge_stops)} of {count} quick API calls were stopped by the edge "
                                           f"({sorted(set(edge_stops))}). Rate-limit sign-in and token endpoints at the edge, not the API.")
    return Check("burst", title, PASS, f"{count} quick API calls all reached OpenClear.")


def report(checks: list[Check]) -> str:
    width = max(len(c.title) for c in checks)
    return "\n".join(f"{c.status:4}  {c.title:<{width}}  {c.detail}" for c in checks)


def as_json(checks: list[Check]) -> str:
    return json.dumps([asdict(c) for c in checks], indent=2)


def failed(checks: list[Check]) -> bool:
    return any(c.status == FAIL for c in checks)
