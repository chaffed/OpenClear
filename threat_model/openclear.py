# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""OpenClear's threat model as code (OWASP_PLAN.md OW-D12), with OWASP pytm.

`build()` returns the model: who and what talks to OpenClear, across which trust boundaries,
carrying which data. pytm matches it against its threat library (CAPEC-based) to list
generic threats per element. `OPENCLEAR_THREATS` is the part pytm can't know: threats
specific to positive pay, each with what mitigates it. `scripts/render_threat_model.py` renders
both into docs/security/threat-model.md; a test fails when that page is stale.

Change this file when a trust boundary, an outside system or a data flow is added or
removed (the PR checklist asks)."""

from dataclasses import dataclass

from pytm import TM, Actor, Boundary, Classification, Data, Dataflow, Datastore, ExternalEntity, Process, Server


@dataclass(frozen=True)
class Threat:
    id: str
    stride: str  # S, T, R, I, D or E
    boundary: str
    threat: str
    mitigation: str
    evidence: str  # controls (docs/compliance/controls.md), files, ASVS
    status: str  # "Mitigated", "Partly", "Service provider's (CUEC)", "Bank's (CUEC)"


def build() -> TM:
    tm = TM(
        name="OpenClear",
        description="Positive pay (checks and ACH) for banks and their business customers, run by a "
        "service provider for one or more banks.",
        isOrdered=False,
    )

    # --- Trust boundaries ---
    internet = Boundary(name="Internet")
    bank = Boundary(name="Bank's network")
    edge = Boundary(name="Edge (WAF / CDN)")
    installation = Boundary(name="OpenClear installation (service provider)")
    stores = Boundary(name="Data stores (service provider)")
    outside = Boundary(name="Outside services")

    # --- People ---
    bank_staff = Actor(name="Bank staff", inBoundary=internet)
    customer_user = Actor(name="Customer user", inBoundary=internet)
    platform_staff = Actor(name="Platform staff", inBoundary=internet)

    # --- Outside systems ---
    online_banking = ExternalEntity(name="Embedding host (online banking)", inBoundary=bank)
    core = ExternalEntity(name="Bank core and API clients", inBoundary=bank)
    idp = ExternalEntity(name="Identity provider (OIDC / SAML)", inBoundary=bank)
    directory = ExternalEntity(name="Directory (SCIM client)", inBoundary=bank)
    siem = ExternalEntity(name="SIEM", inBoundary=bank)
    webhook = ExternalEntity(name="Webhook receiver", inBoundary=bank)
    messaging = ExternalEntity(name="Email and SMS providers", inBoundary=outside)
    cloud_ocr = ExternalEntity(name="Cloud OCR (optional)", inBoundary=outside)
    release_feed = ExternalEntity(name="Release feed (GitHub)", inBoundary=outside)

    # --- OpenClear ---
    waf = Server(name="Edge / WAF", inBoundary=edge)
    waf.controls.validatesHeaders = True
    waf.controls.handlesResourceConsumption = True

    app = Server(name="OpenClear web and API", inBoundary=installation, usesSessionTokens=True, usesXMLParser=True)
    for control in (
        "authenticatesSource", "authorizesSource", "checksInputBounds", "definesConnectionTimeout", "disablesDTD",
        "encodesHeaders", "encodesOutput", "encryptsSessionData", "handlesResourceConsumption", "hasAccessControl",
        "implementsAuthenticationScheme", "implementsCSRFToken", "implementsNonce", "implementsPOLP",
        "implementsServerSideValidation", "implementsStrictHTTPValidation", "isHardened", "providesIntegrity",
        "sanitizesInput", "usesParameterizedInput", "usesStrongSessionIdentifiers", "validatesContentType",
        "validatesHeaders", "validatesInput", "verifySessionIdentifiers", "handlesCrashes",
    ):
        setattr(app.controls, control, True)
    app.controls.usesMFA = True  # optional per bank today; required-MFA is OW3b

    worker = Process(name="Background worker", inBoundary=installation)
    worker.controls.handlesResourceConsumption = True
    worker.controls.handlesCrashes = True
    worker.controls.implementsPOLP = True

    plugins = Process(name="Plugins (in process)", inBoundary=installation)

    database = Datastore(
        name="Database (Postgres, row-level security)", inBoundary=stores, isSQL=True, storesPII=True,
        storesSensitiveData=True, storesLogData=True, hasWriteAccess=True, isShared=False,
    )
    database.controls.isEncryptedAtRest = True  # service provider's (OC-SC-02)
    database.controls.hasAccessControl = True
    database.controls.usesParameterizedInput = True

    files = Datastore(
        name="File storage (local, S3, Azure)", inBoundary=stores, storesPII=True, storesSensitiveData=True,
        hasWriteAccess=True,
    )
    files.controls.isEncryptedAtRest = True
    files.controls.providesIntegrity = True  # signed files (OC-SC-03)

    redis = Datastore(name="Redis (rate limits, optional)", inBoundary=stores, hasWriteAccess=True)
    keys = Datastore(name="Keys and secrets (files / environment)", inBoundary=stores, storesSensitiveData=True)

    # --- Data ---
    credentials = Data(name="Passwords and security-key assertions", classification=Classification.SECRET,
                       isCredentials=True)
    session_tokens = Data(name="Session tokens", classification=Classification.SECRET, isCredentials=True)
    api_credentials = Data(name="API client secrets and signed assertions", classification=Classification.SECRET,
                           isCredentials=True)
    sso = Data(name="SSO assertions and ID tokens", classification=Classification.SECRET, isCredentials=True)
    account_data = Data(name="Accounts, issued items and ACH data", classification=Classification.RESTRICTED,
                        isPII=True, isStored=True)
    check_images = Data(name="Check images", classification=Classification.RESTRICTED, isPII=True, isStored=True)
    payment_files = Data(name="Payment files (X9.37, NACHA, CSV, Excel, zip)", classification=Classification.RESTRICTED,
                         isPII=True, isStored=True)
    decisions = Data(name="Exceptions and pay / return decisions", classification=Classification.RESTRICTED,
                     isStored=True)
    action_log = Data(name="Action log", classification=Classification.SENSITIVE, isStored=True)
    events = Data(name="Security events", classification=Classification.SENSITIVE)
    notices = Data(name="Notifications", classification=Classification.SENSITIVE, isPII=True)
    people = Data(name="People and group memberships", classification=Classification.SENSITIVE, isPII=True)

    # --- Flows ---
    def flow(source, sink, name, data, protocol="HTTPS", encrypted=True, **controls):
        f = Dataflow(source=source, sink=sink, name=name, protocol=protocol, data=data)
        f.controls.isEncrypted = encrypted
        for key, value in controls.items():
            setattr(f.controls, key, value)
        return f

    flow(bank_staff, waf, "Bank staff use the web UI", [credentials, session_tokens, account_data, decisions])
    flow(customer_user, waf, "Customer users use the web UI", [credentials, session_tokens, account_data, decisions])
    flow(platform_staff, waf, "Platform staff use the operator view", [credentials, session_tokens])
    flow(online_banking, waf, "Embedded session launched from online banking", [sso, session_tokens])
    flow(core, waf, "Core and API clients call the API", [api_credentials, account_data, payment_files, decisions])
    flow(idp, waf, "SSO sign-in responses (browser-relayed)", [sso])
    flow(directory, waf, "SCIM provisioning", [api_credentials, people])
    flow(waf, app, "Edge forwards to the origin", [credentials, session_tokens, account_data, payment_files, decisions],
         authenticatesSource=True)
    flow(app, idp, "OIDC discovery, token and JWKS requests", [sso], authenticatesDestination=True)
    flow(app, database, "Queries, scoped to one bank", [account_data, decisions, action_log, people], protocol="SQL")
    flow(app, files, "Uploads, images and exports", [check_images, payment_files])
    flow(app, redis, "Rate-limit counters", [], protocol="RESP")
    flow(app, keys, "Signing and encryption keys read at start", [], protocol="file", encrypted=False)
    flow(app, plugins, "Plugin hooks", [account_data, decisions], protocol="in-process", encrypted=False)
    flow(worker, database, "Jobs read and write, per bank", [account_data, decisions, action_log], protocol="SQL")
    flow(worker, files, "Exports, archives, OCR input", [check_images, payment_files, action_log])
    flow(worker, siem, "Security events (HEC, syslog over TLS)", [events], authenticatesDestination=True)
    flow(worker, webhook, "Signed webhooks", [decisions], authenticatesDestination=True)
    flow(worker, core, "Decisions delivered to the core", [decisions], authenticatesDestination=True)
    flow(worker, messaging, "Email and SMS", [notices], authenticatesDestination=True)
    flow(worker, cloud_ocr, "Check images for OCR (if configured)", [check_images], authenticatesDestination=True)
    flow(app, release_feed, "Release check", [])

    return tm


OPENCLEAR_THREATS: tuple[Threat, ...] = (
    Threat("OC-T01", "E", "Bank ↔ bank", "One bank's people or data reach another bank.",
           "Every query runs in one bank's context; Postgres row-level security enforces it under a non-superuser role.",
           "OC-AC-09; db/tenancy.py; tests/test_postgres/test_rls_*.py; ASVS 8.4.1", "Mitigated"),
    Threat("OC-T02", "E", "Customer ↔ customer", "A customer's people see another customer's accounts, items or images.",
           "Customer- and account-scoped repositories on every query; per-account access.",
           "OC-AC-03, OC-AC-04; repositories/; db/account_scope.py; ASVS 8.2.2", "Mitigated"),
    Threat("OC-T03", "S", "Bank ↔ bank", "A session from one organization's sign-in is used to enter another organization "
           "the same person belongs to, without that organization's sign-on rules.",
           "Sessions record how they were signed in; switching is allowed only where that sign-in is accepted.",
           "web/routers/tenant_switch.py::switch_refusal; 1.21.2; ASVS 6.3.4, 6.8.1", "Mitigated"),
    Threat("OC-T04", "S", "Identity provider", "A bank's or customer's identity provider asserts someone it doesn't vouch for.",
           "A provider links only people already members in its own scope; groups are re-read on every sign-in; "
           "a customer's sign-on is confined to that customer.",
           "OC-IA-03, OC-IA-05; services/sso_service.py; SECURITY_REVIEW.md R2; ASVS 6.8.1",
           "Partly (matching by subject: issue #6)"),
    Threat("OC-T05", "S", "Internet", "Password guessing and credential stuffing.",
           "Lockout, per-person and per-IP rate limits, the bank's password policy, security keys; breached-password "
           "checks planned.",
           "OC-IA-01, OC-IA-02, OC-SC-06; OWASP_PLAN.md OW3a; ASVS 6.2.12, 6.3.1", "Partly (OW3a)"),
    Threat("OC-T06", "S", "Internet", "A stolen session cookie is replayed.",
           "HttpOnly, Secure, SameSite cookies; idle and absolute timeouts; server-side revocation; token version bump "
           "on password change.",
           "OC-AC-10, OC-SC-04; services/session_service.py; ASVS 7.x", "Mitigated"),
    Threat("OC-T07", "T", "Customer ↔ bank", "One person issues and approves the same payment decision, or weakens a "
           "protection alone.",
           "Segregation of duties; dual control on decisions; two people for settings changes, at bank, customer and "
           "service provider.",
           "OC-AC-05, OC-AC-06, OC-AC-07, OC-AC-13; DUAL_CONTROL_PLAN.md", "Mitigated"),
    Threat("OC-T08", "T", "Bank's network", "A forged or altered payment file (X9.37, NACHA, CSV) creates false issued items "
           "or presentments.",
           "Uploads only by permitted people or API clients; hardened parsers; every file kept with its SHA-256 and a "
           "signature; row-by-row results.",
           "OC-SI-01, OC-SI-02, OC-SC-03", "Mitigated"),
    Threat("OC-T09", "T", "Data stores", "The action log is altered to hide a decision or a settings change.",
           "Chained, signed entries; verified daily; archived to signed files; the provider's own log likewise.",
           "OC-AU-01, OC-AU-02, OC-AU-03", "Mitigated"),
    Threat("OC-T10", "R", "Customer ↔ bank", "Someone denies having decided an exception or changed a setting.",
           "Every change in the action log with who, when, from where and the request id; signed evidence export.",
           "OC-AU-01, OC-AU-05", "Mitigated"),
    Threat("OC-T11", "I", "Internet", "Account numbers or check images leak through the browser, caches or URLs.",
           "no-store on the UI and API; CSP; no third-party scripts; access limited by customer and account.",
           "OC-SC-04; web/security_headers.py; ASVS 14.2, 14.3", "Partly (issues #13, #14)"),
    Threat("OC-T12", "I", "Outside services", "Data sent to an outside service (cloud OCR, email, SMS, SIEM, webhooks) is "
           "exposed or sent somewhere unintended.",
           "Each service is off unless configured, reviewed per bank; outbound connections only to public addresses; "
           "webhooks signed; no secrets in events.",
           "OC-SC-07, OC-AU-04; services/outside_services.py", "Mitigated"),
    Threat("OC-T13", "I", "Outside services", "A webhook, SIEM or SSO URL is pointed at an internal address (SSRF).",
           "The address is checked on the connection itself, so DNS rebinding doesn't get past it.",
           "OC-SC-07; auth/outbound_http.py; ASVS 1.3.6", "Mitigated"),
    Threat("OC-T14", "E", "Service provider", "Platform staff read or change a bank's data.",
           "Support access only through a time-limited grant the bank makes; two people at the service provider for "
           "provider actions; the provider's own signed log.",
           "OC-AC-08, OC-AC-13; ADR 0004", "Mitigated"),
    Threat("OC-T15", "E", "Installation", "A plugin misbehaves or is malicious.",
           "Plugins are installed and enabled by the service provider and each bank; they run in process, so they are "
           "trusted code; what's enabled is in the evidence export.",
           "OC-CM-03; docs/PLUGINS.md", "Service provider's (CUEC)"),
    Threat("OC-T16", "T", "Data stores", "A swapped ML model file runs code or skews decisions.",
           "Model files are checked against a signed digest before loading; activation needs two people.",
           "ml/registry.py; OC-AC-13; SR 11-7 page", "Mitigated"),
    Threat("OC-T17", "D", "Internet", "Large or malicious uploads (zip bombs, huge images) exhaust the server.",
           "Request size, declared and actual zip size, and image pixel limits; long work runs as jobs with time limits.",
           "OC-SI-01; bulk_import/zip_import.py, bulk_import/images.py; ASVS 5.2", "Mitigated"),
    Threat("OC-T18", "D", "Bank's network", "Decisions miss the return deadline because OpenClear or the core is down.",
           "Deadlines shown with reminders; an outbox delivers decisions with retries; health and job status reported.",
           "OC-SI-05, OC-SI-06, OC-CA-02", "Mitigated"),
    Threat("OC-T19", "S", "Edge", "Requests bypass the WAF by reaching the origin directly, or forge the caller's address.",
           "The caller's address is taken only from the configured edge; an edge secret; the edge guide.",
           "OC-SC-05; docs/guides/edge.md; scripts/edge_check.py", "Partly (service provider locks the origin)"),
    Threat("OC-T20", "I", "Data stores", "Keys or secrets leak from the server.",
           "Production refuses checked-in keys; secrets in the database are encrypted with a rotatable key; keys "
           "rotated on the bank's schedule.",
           "OC-CM-01, OC-SC-01; auth/crypto.py; issue #10", "Service provider's (CUEC)"),
    Threat("OC-T21", "T", "Bank's network", "An ACH debit from an unauthorized originator is paid.",
           "Debits checked against the customer's authorized originators; unauthorized debits go to exception with a "
           "signed Written Statement of Unauthorized Debit.",
           "OC-AC-12, OC-SI-07; docs/compliance/nacha.md", "Mitigated"),
    Threat("OC-T22", "I", "Embedding host", "OpenClear is framed by a site that isn't the bank's online banking "
           "(clickjacking) or leaks messages to it.",
           "frame-ancestors limited to the bank's configured origin; messages posted only to that origin.",
           "web/security_headers.py; static/js/app.js; ASVS 3.4.6", "Mitigated"),
)
