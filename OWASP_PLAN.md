# Following the OWASP Developer Guide

The [OWASP Developer Guide](https://devguide.owasp.org/) describes how to build software
securely. It covers requirements, design, implementation, verification, operations,
culture and gap analysis, and for each one it points to an OWASP project: ASVS, threat
modeling tools, the cheat sheets, CycloneDX and Dependency-Check, WSTG and ZAP, the Core
Rule Set, and SAMM. This plan applies that guidance to OpenClear.

**Purpose: hardening first, then evidence.** The assessment is honest, fixes come before
claims, and anything shown to a bank states only what's proven. Banks' third-party risk
reviews ask for this by name ("Which ASVS level?", "Send us your SBOM", "Do you run SAST and
DAST?"), so the results also go into the [TPRM due-diligence pack](docs/compliance/tprm.md).
That pack has no penetration-test report, SBOM or disclosure policy today.

**Scheduling.** OW1 and OW2 run now. They're mostly documents and CI and don't block feature
work. OW3 onward runs alongside feature work.

## What exists today, by the guide's checklist

| Guide section | Today | Where |
|---|---|---|
| Secure by default | The app won't start in production with the checked-in keys. The sandbox and demo are the only places with relaxed settings. | `config.py::assert_production_safe` |
| Frameworks and libraries | Jinja autoescape, nh3 for markdown, SQLAlchemy bound parameters. Every `text()` is a literal with parameters. | `web/templates.py`, `services/` |
| Secure database access | Row-level security per bank, scoped repositories per customer and account. | `db/tenancy.py`, `db/account_scope.py`, `repositories/` |
| Encode and escape | A CSP with a nonce on every response; no inline script. | `web/security_headers.py` |
| Validate inputs | Pydantic on the API. Size limits on requests, zip files (declared size) and images (decompression bombs). | `main.py`, `bulk_import/zip_import.py`, `bulk_import/images.py` |
| Digital identity | bcrypt, capped at 72 bytes; lockout; WebAuthn, step-up sign-in, SSO (OIDC, SAML), SCIM, `private_key_jwt` clients. The password policy defaults to 8 characters with no composition rules (ASVS 6.2.5 met); a bank can make it stricter and a customer stricter still. WebAuthn can only be required per membership (`require_webauthn`, for multi-customer access). | `auth/`, `domain/tenant.py`, `domain/tenant_membership.py` |
| Access control | Permissions, separation of duties, dual control for settings, per-account access. | `auth/permissions.py`, `DUAL_CONTROL_PLAN.md` |
| Protect data | Signed action log, encrypted SSO secrets, signed ML artifacts, HSTS, cookies that are HttpOnly, Secure and SameSite. | `services/audit_log_service.py`, `ml/registry.py`, `web/security.py` |
| Logging and monitoring | A catalog of security events, SIEM delivery, control status. | `services/security_events.py`, `SIEM_PLAN.md` |
| Errors and exceptions | Exception handlers for HTTP, validation and database errors. | `main.py` |
| Outbound requests (SSRF) | Webhook, SIEM and SSO fetches connect only to public addresses. The check is made on the connection itself. | `auth/outbound_http.py` |
| WAF (Operations) | An edge guide covering the Core Rule Set, with exclusions for each path; `edge_check.py`. | `docs/guides/edge.md`, `WAF_PLAN.md` |
| Secure headers (OSHP) | CSP, X-Frame-Options or `frame-ancestors`, HSTS, Permissions-Policy, `no-store`. | `web/security_headers.py` |

## Gaps

| Guide section | Gap |
|---|---|
| Requirements: ASVS, risk profile | No target ASVS level and no assessment against one (scope: `docs/security/asvs-5.0-scope.csv`, 283 requirements, from OWASP's own CSV). No written data classification or risk profile. |
| Design: threat modeling | No threat model, no data-flow diagram, no privacy review (LINDDUN). |
| Implementation: dependencies | `pyproject.toml` sets only lower bounds (`>=`), and there's no lock file, so each install can differ. No SCA, no SBOM, no Dependabot. The vendored JS (`static/js/vendor/`) isn't tracked anywhere, and neither are the SDKs' own dependencies. |
| Implementation: DevSecOps pipeline | No SAST and no secret scanning. The Actions are pinned by tag rather than SHA. `tests.yml` doesn't declare `permissions:`. The base images are pinned by tag rather than digest. |
| Digital identity: passwords (ASVS 6.2) | No breached-password check (6.2.12, L2). Existing passwords are never re-checked. The 8-character default meets ASVS's minimum but not NIST 800-63B-4 for single-factor sign-in (15). |
| Digital identity: MFA (ASVS 6.3.3, L2) | A bank can't require a second factor for password sign-ins. A user who loses their key can't be reset by an admin; only the user can remove their own keys. |
| Verification: WSTG, DAST | No DAST. Security tests exist, but nothing ties them to the requirements they prove. |
| Operations | `docs/guides/edge.md` tells service providers which CRS rules to exclude, but nothing tests those exclusions against the real CRS. No `security.txt`, no `SECURITY.md`. |
| Vulnerability management | No disclosure route and no private place to handle findings; no code-scanning alerts. |
| Gap analysis: SAMM | No maturity baseline. |

The ASVS assessment (OW1) will add to this list.

## Scope

- **Server** (`src/openclear`, the Docker image, CI): everything in this plan.
- **SDKs** (`sdks/python`, `typescript`, `dotnet`, `embed`): dependency scanning (Dependabot)
  and CodeQL only. The ASVS assessment covers the server.
- **Plugins:** the plugin interface is a trust boundary in the threat model, and
  `docs/PLUGINS.md` gains a section on writing a safe plugin. Plugin code itself belongs to
  whoever writes it and to the service provider who chooses to run it.
- **The sandbox on Fly:** never scanned from CI. One manual ZAP run against it per release.

## Not applicable, and why

| Guide section | Why |
|---|---|
| MAS, MASVS, MASTG, MASWE, Mobile Top 10 | OpenClear has no native mobile app; the phone support in `MOBILE_PLAN.md` is the responsive web UI, which ASVS covers. Revisit if a native app is ever built. |
| ESAPI, CSRFGuard, Java encoders | Java libraries. Their Python equivalents are already in place (Jinja autoescape, nh3, OpenClear's own CSRF token). |
| ASVS V17 (WebRTC) | OpenClear doesn't use WebRTC. |
| Security champions, training, Snakes and Ladders | They're for teams. With one maintainer, the PR checklist and the CLAUDE.md conventions (OW5) do that job. |
| SAMM practices beyond six | SAMM's governance, training and organisational practices score low by definition with one maintainer. Only the six this plan acts on are scored (OW-D11). |
| Amass, Nettacker, OWTF, secureCodeBox, BLT | These are for scanning a deployed estate, which is each service provider's job. The edge guide can point to them. |
| Dependency-Track, DefectDojo | Both are servers someone would have to host. GitHub's dependency graph, code scanning and private advisories cover the same ground for one public repo. Service providers who already run Dependency-Track can load the SBOM into it. |
| SecurityRAT | It manages requirements in Jira, which OpenClear doesn't use. The ASVS crosswalk links each requirement to OpenCRE instead. |

## Decisions

### Requirements and assessment

- **OW-D1. Target ASVS 5.0 Level 2, and Level 3 in the chapters that protect money and
  identity:** V6 Authentication, V7 Session, V8 Authorization, V9 Tokens, V10 OAuth/OIDC,
  V11 Cryptography, V16 Logging. L3 requirements that need the service provider's infrastructure (an
  HSM, for example) are marked Service provider's (CUEC).
- **OW-D2. The assessment is done by OpenClear itself, now.** It's worked through chapter by
  chapter (one findings file per chapter), then reviewed. An independent penetration test
  joins `MASTER_PLAN.md`'s list of things waiting for a first client to fund. Once one is
  done, its report goes into the TPRM pack.
- **OW-D3. The ASVS crosswalk lives in `docs/compliance/asvs.md`, next to the other
  crosswalks.** Each requirement gets a status (Met, Partly, Not met, Service provider's (CUEC),
  Not applicable) and its evidence: a test, a file or a control ID. Rows cite OpenCRE IDs.
- **OW-D4. Tests prove requirements by marking them:** `@pytest.mark.asvs("6.2.12")`.
  `scripts/asvs_crosswalk.py` builds the evidence column from those marks, so the page can't
  claim a test that doesn't exist. A test fails if a Met row has no mark and no file.
- **OW-D5. Gaps stay private until they're fixed.** Working notes from the assessment live in
  `security-findings/`, which git ignores. The repo is public, so an exploitable
  finding becomes a private GitHub security advisory, never a public issue. A finding that
  can't be exploited (missing documentation, a gap in the pipeline) becomes an ordinary
  issue labelled `security`. `asvs.md` is published once OW3 lands, and its remaining "Not
  met" rows are only ones that can't be exploited.
- **OW-D6. Reports come in through GitHub private vulnerability reporting.** There's no
  OpenClear domain, so there's no `security@` address yet. `SECURITY.md` explains the GitHub
  route, and that a bank should contact the service provider it buys from first. A domain and
  `security@` come with the first client. Each service provider's `security.txt` names its own contact.

### Pipeline

- **OW-D7. Everything runs in GitHub, with no servers to host.** CodeQL (free for public
  repos) and Bandit for SAST, `pip-audit` and Dependabot for SCA, gitleaks for secrets, ZAP
  through its Action. Results go to code scanning as SARIF. These are security scans, not a
  style linter; CLAUDE.md's "no linter" still holds.
- **OW-D8. What fails a build:**

  | Check | Fails the build on | Otherwise |
  |---|---|---|
  | `pip-audit` | a known vulnerability with a fix available | warns, with a waiver (reason and expiry) in `.pip-audit-waivers.toml` |
  | CodeQL, Bandit | new high or critical findings | reported in code scanning; the starting baseline is recorded, each suppression with a reason |
  | gitleaks | any finding | — |
  | ZAP baseline (passive, every PR) | new medium findings or worse | waived in `zap-rules.tsv` with a reason |
  | ZAP active scan (weekly and before releases) | never blocks | opens issues, or advisories under OW-D5 |

- **OW-D9. Lock files with hashes, for every installer.** `uv pip compile --generate-hashes
  --python-platform linux` writes `requirements/{base,dev,extras}.txt` from `pyproject.toml`
  (uv builds the Linux lock from macOS; installers need only pip). The Dockerfile, CI and the
  README's source-install steps all use `pip install --require-hashes -r ...`.
  `pyproject.toml` keeps its ranges.
- **OW-D10. Dependabot weekly, grouped:** one PR each for Python, GitHub Actions, Docker and
  each SDK. Security updates come at once and ungrouped. Nothing merges itself, because the
  local Postgres and Linux suites must run first. Updates go into the next feature merge, or
  a patch release if a security fix is urgent.
- **OW-D11. SBOMs in CycloneDX JSON**, from `cyclonedx-py` plus a hand-kept component list
  for `static/js/vendor/`. One per release goes on the GitHub release, in the TPRM pack and in
  the evidence export.

### Design

- **OW-D12. Threat model as code with pytm** (`threat_model/openclear.py`). It's text in the
  repo, reviewed like code, and it generates the data-flow diagram and the threat list into
  `docs/security/threat-model.md`. A test fails if the generated page is stale. LINDDUN GO is
  a one-off privacy pass, recorded on the same page.
- **OW-D13. SAMM for six practices only:** threat assessment, security requirements, security
  testing, defect management, secure build and secure deployment. Score them in OW1, set a
  12-month target, and re-score after a year.
- **OW-D14. Security metrics go in each release's notes and in the TPRM pack**, generated at
  release: ASVS coverage (Met / applicable), advisories fixed in the release, how old the
  dependencies are, the SBOM link. Not on the operator view, which belongs to an installation
  rather than to the project.

### Passwords (OW3a)

- **OW-D15. Breached passwords.** A bundled list of the 100,000 most common breached passwords
  (about 1 MB of sorted SHA-1 prefixes) is always checked. A service provider can point
  `breached_password_list` at a bigger list (such as HIBP's full download) in its own storage,
  for installations that can't reach the internet. The HIBP range API (k-anonymity, through
  `outbound_http`) is optional per installation. If HIBP can't be reached, the bundled or
  loaded list decides, and a log entry is written.
- **OW-D16. A new bank's minimum length is worked out, not stored: 8 characters if the bank
  requires MFA (OW-D18), otherwise 15** (NIST 800-63B-4). A bank can set its own number
  instead, which always wins. Existing banks keep their stored 8 as their own number
  (`tenant.password_min_length` becomes nullable; null means worked out). Only the bank's MFA
  setting counts: a customer that requires MFA for itself gains a second factor but never a
  shorter minimum, so a customer's policy is still never weaker than its bank's. No
  composition rules by default; they stay available. The policy page shows which minimum
  applies and why.
- **OW-D17. Existing passwords are checked at sign-in.** If a password that has just verified
  is breached, or shorter than the current policy, the user is let in, but
  `must_change_password` is set and they change it before going further. A security event
  records which reason applied.

### Required MFA (OW3b)

- **OW-D18. A bank can require a security key for password sign-ins.** The setting's label
  says "Require a security key or passkey"; the term is security key (`CONTEXT.md`). It's set
  at bank level, and a customer can turn it on for itself even if its bank doesn't (never off
  when the bank has it on). It's a sensitive setting under dual
  control. A control check is WARN while it's off, and while users are still inside the grace
  period without a key (giving their number).
- **OW-D19. WebAuthn is the only second factor**, passkeys and built-in authenticators (Touch
  ID, Windows Hello, Android) included. No TOTP and no SMS, because both can be phished.
- **OW-D20. A grace period when the requirement is turned on:** the bank chooses it (default 14
  days, at most 90). Until it ends, users without a key see a banner counting down the days.
  After it, they must register a key at sign-in before going further.
- **OW-D21. An admin can reset a user's second factor.** Anyone with `user:manage` (a customer
  admin for their own customer's users) can remove all of a user's keys. The user must then
  register a new one at their next sign-in. The admin must record how they confirmed the
  person's identity (ASVS 6.4.4: proofing at the same level as enrollment). The reset is an audited action and an
  `auth.mfa.reset` security event, and the user is emailed. It isn't under dual control,
  because waiting for a second admin near the cutoff costs the customer money. If the person
  who lost a key is a bank's only administrator, platform staff can reset them, as a
  protected change under the service provider's dual control (DC4). The bank's other
  administrators (or the person, if there are none) are emailed, and the same event is
  raised. It's the only time platform staff act on a bank's user without a support access
  grant ([ADR 0004](docs/adr/0004-who-must-use-a-security-key-and-who-can-reset-one.md)).
- **OW-D22. Exemptions:**

  | Who | Why |
  |---|---|
  | SSO users | MFA is the identity provider's job; new CUEC: the bank's IdP enforces MFA. |
  | Embedded sessions | The embedding host already signed the user in; new CUEC on the embedding host's sign-in. |
  | API clients | They aren't people. |

  Platform staff are **never** exempt: their sign-in always requires a security key, whatever
  the banks' settings.
- **OW-D23. Platform staff's security keys live in their own table,** `platform_webauthn_credential`,
  read only by the platform pages and without row-level security (it holds no bank's data).
  A bank's keys stay in `webauthn_credential`, whose policies are unchanged. New platform staff
  register a key at their first sign-in ([ADR 0005](docs/adr/0005-platform-staff-keys-in-their-own-table.md)).
- **OW-D24. The minimum password length stays a stored number** (OW-D16 as built in 1.22.0).
  When a bank requires a security key, its password-policy page says NIST allows 8 characters
  and offers to lower it; lowering is a loosening, under dual control.
- **OW-D25. An SSO connection can require its provider to report MFA,** off by default: OIDC
  `amr`/`acr`, SAML `AuthnContextClassRef`. A sign-in without it is refused. Turning it on is a
  tightening (at once); off is a loosening (dual control). With it on, the exemption in
  OW-D22 is verified rather than trusted.
- **OW-D26. A bank can require API clients to sign with a key pair** (`private_key_jwt`). Clients
  using a secret stop working until switched. DPoP and mTLS-bound tokens are not built: ASVS
  10.3.5 and 10.4.14 are recorded as not met at Level 3, as no bank's core can send them today.

### Recorded as not met at Level 3

- **OW-D27. Adaptive controls** (8.1.3, 8.1.4, 8.2.4, 8.4.2): time, place, IP and device in
  access decisions. OpenClear sits behind each bank's edge and identity provider, which are
  better placed for them; required MFA and new-device emails (OW-D30) cover the main risk. A
  per-bank IP allowlist can follow if a bank asks.
- **OW-D28. Logging every read of sensitive data** (16.3.2 at Level 3): exports (already audited)
  and check-image downloads are logged in the action log; other pages that show sensitive data
  aren't, to keep the log useful. Partly met.

### Sessions and identity (1.24.0)

- **OW-D29. A table of active sessions:** started, last used (updated on refresh), browser (from
  its user agent) and IP, kept 30 days after the session ends. A person sees and ends their own
  sessions on Security; an admin sees a person's on their page; bank admins, and customer
  admins for their customer, can "Sign out everyone" (a tightening: at once, audited).
  Concurrent sessions stay unlimited, with the reason documented (ASVS 7.1.2, 7.4.5, 7.5.2).
- **OW-D30. An email after every sign-in from a new device,** always sent. A device is a
  long-lived random `__Host-device` cookie remembered against the person; the email names the
  browser, IP and time (ASVS 6.3.5).
- **OW-D31. An email address can change; the person is their UUID.** Email stays what people
  type to sign in. It can change by four routes, each emailing both the old and new address
  and raising `user.email.changed`
  ([ADR 0006](docs/adr/0006-email-is-a-changeable-attribute.md)):
  - the person, from Security, after a recent sign-in, confirmed by a link to the new address;
  - an administrator, for someone only in their organization, also confirmed by the new address;
  - the bank's directory (SCIM), applied directly;
  - SSO: a known subject with a new email moves to it when the new domain is verified for
    that bank or customer; otherwise refused, as in 1.22.1.
- **OW-D32. A bank can verify its own email domains,** with the same TXT-record check customers
  use (an entry with no customer is the bank's).
- **OW-D33. "Move everyone to a new domain"** for a customer or bank renamed: a protected change,
  only to a verified domain, skipping people who belong to other organizations (listed for their
  own confirmation).

### Files (1.25.0)

- **OW-D34. Malware scanning: a CUEC plus two hooks.** The CUEC: the bank uses commercially
  standard malware detection. The hooks, each optional:
  - **ClamAV** (`clamd`): scans every upload; if it can't answer, the upload is refused.
  - **VirusTotal: hash lookups only, never uploads,** since anything uploaded there is shared with
    third parties ([ADR 0007](docs/adr/0007-virustotal-hash-lookups-only.md)). If it can't answer,
    the upload is accepted, logged, and the hash re-checked later. Each can be set to refuse
    instead.
  - On a detection: the upload is refused (or, found later, the file is quarantined), with a
    high-severity `file.malware_detected` event, an action-log entry, and an email to the
    bank's admins.
- **OW-D35. Account numbers stay out of URLs:** list filters take account ids; global search is a
  POST whose results page doesn't carry the query; the edge guide says to mask query strings in
  edge logs.

## Stages

Each stage is one merge to `main` with one run of the three suites, following
`MASTER_PLAN.md`.

| Stage | Guide sections | What lands | Migration | Outside checks |
|---|---|---|---|---|
| **OW1. Baseline** | Requirements; Design; Gap analysis | • Risk profile and data classification (account numbers, check images, PII, credentials, keys) in `docs/security/risk-profile.md`.<br>• ASVS 5.0 assessment (OW-D1, OW-D2), written to `docs/compliance/asvs.md` but not linked from the compliance index until OW3 (OW-D5). It comes with the first `asvs` marks and `scripts/asvs_crosswalk.py` (OW-D3, OW-D4).<br>• pytm threat model (OW-D12) covering each trust boundary: browser, embedding host, bank core API and SDK clients, IdP (OIDC/SAML), SCIM directory, edge, worker, file storage, payment-file ingestion (X9.37, NACHA, zip), ML artifacts, plugins, SIEM and webhook destinations, service provider and platform staff. Plus a LINDDUN GO pass.<br>• SAMM self-assessment for six practices (OW-D13) in `docs/security/samm.md`.<br>• GitHub private vulnerability reporting turned on, and `SECURITY.md` (OW-D6).<br>• **Output: the fix list**, as private advisories or `security` issues (OW-D5). | None | None |
| **OW2. Pipeline** | Implementation; Operations (DevSecOps) | • Lock files (OW-D9), used by the Dockerfile, CI and the README.<br>• `pip-audit`, CodeQL, Bandit, gitleaks with their gates (OW-D7, OW-D8), plus an optional gitleaks pre-commit hook and a one-off scan of the full history.<br>• Dependabot (OW-D10).<br>• Actions pinned to SHAs; `permissions: contents: read` on each workflow; base images pinned by digest.<br>• CycloneDX SBOM on release (OW-D11), added to the release steps in CLAUDE.md. | None | A full-history gitleaks scan, once. Any real secret it finds gets rotated before the merge. |
| **OW3a. Passwords and disclosure** | Digital identity; Operations | • Breached-password check (OW-D15).<br>• New defaults for new banks (OW-D16).<br>• Passwords checked at sign-in (OW-D17).<br>• `/.well-known/security.txt` from each service provider's configuration.<br>• The small, self-contained fixes from OW1's list. | `tenant.password_min_length` nullable (OW-D16) | None |
| **OW3b. Required MFA (1.23.0)** | Digital identity | • The bank and customer setting with dual control and its control check (OW-D18, OW-D19).<br>• Grace period and banner (OW-D20).<br>• Admin reset (OW-D21).<br>• Exemptions and their CUECs (OW-D22); platform staff keys and required sign-in (OW-D23).<br>• The password-policy hint (OW-D24); the SSO MFA check (OW-D25); key-pair-only API clients (OW-D26).<br>• The 1.22.1 fallbacks removed (old cookie names, tokens without `iss`/`aud`).<br>• Documentation issues #3, #10, #15; `asvs.md` built and linked from the compliance index (OW-D5), with OW-D26–D28 as recorded Level 3 gaps. | The MFA settings (bank, customer, grace period, when it was turned on); `platform_webauthn_credential`; the SSO and API-client switches | Keycloak live check with an MFA claim |
| **OW3c. Sessions and identity (1.24.0)** | Digital identity; Session management | • The sessions table and its pages (OW-D29).<br>• New-device emails (OW-D30).<br>• Email changes by all four routes (OW-D31).<br>• Bank email domains (OW-D32) and the domain move (OW-D33). | Sessions, devices, pending email changes, `customer_email_domain.customer_id` nullable | Keycloak: an email change at the provider |
| **OW4. Files and verification (1.25.0)** | Verification (WSTG, DAST, OSHP); Operations (CRS); File handling | • ClamAV and VirusTotal hooks, quarantine and the CUEC (OW-D34).<br>• Account ids in URLs, search by POST (OW-D35); exports and image downloads in the action log (OW-D28).<br>• ZAP baseline and active scans (OW-D8) in CI against the app in Docker, signed in as a bank admin and a customer user, from `openapi/openapi.json`.<br>• A WSTG checklist for changes that touch sign-in, sessions, permissions or uploads, in the PR template.<br>• `edge_check.py` gains a real-CRS mode: Coraza with CRS at paranoia levels 1 and 2 in a container, with the suite's legitimate traffic (X9.37 uploads, SAML posts, SCIM filters, webhook URLs, markdown) sent through it to prove the exclusions in `edge.md`.<br>• The first manual ZAP run against the sandbox. | None | CRS container, once in the stage, then in CI if it runs fast enough |
| **OW5. Keeping it up** | Culture; Metrics | • `.github/pull_request_template.md` with a security checklist: new trust boundary → update the threat model; new security behaviour → `asvs` mark; new dependency → a reason.<br>• A CLAUDE.md "Conventions that bite" entry for both.<br>• Release-time metrics (OW-D14) added to the release steps.<br>• "Writing a safe plugin" in `docs/PLUGINS.md`.<br>• Independent pentest added to `MASTER_PLAN.md`'s waiting list (OW-D2).<br>• SAMM re-scored a year after OW1. | None | None |

Releases: **1.22.0** after OW3a (the password check and new defaults, `security.txt`);
**1.22.1** the assessment's `ready-for-agent` fixes; **1.23.0** OW3b (required MFA);
**1.24.0** OW3c (sessions and identity); **1.25.0** OW4 (files and verification). OW5 goes in
whichever release follows.

## Testing

- **OW1:** `tests/test_compliance/test_asvs_crosswalk.py` checks three things: every row's
  evidence exists (file paths and test node IDs), every Met row has evidence, and the
  generated page matches the marks. `tests/test_docs/test_threat_model.py` regenerates the
  pytm output and compares it with the checked-in page.
- **OW2:** CI is the test. A throwaway branch with a vulnerable pin (`jinja2==2.10`) and a
  fake AWS key must fail `pip-audit` and gitleaks. The image is built from the lock files and
  runs the Linux suite.
- **OW3a:**
  - the breached list: a hit, a miss, a prefix collision, and a list loaded by the service provider;
  - HIBP stubbed through `outbound_http`, including a timeout that falls back to the list;
  - the worked-out minimum for new banks, with MFA required and without; a bank's own number
    winning; existing banks keeping 8; a customer requiring MFA never lowering the minimum;
  - sign-in with a breached or short password sets `must_change_password` and raises the
    event;
  - rendered-page checks for `security.txt`.
- **OW3b:**
  - table-driven tests over bank on/off × customer on/off × SSO, embedded, API client and
    platform staff, in the style of `test_dual_control_matrix.py`;
  - the grace period at its start, middle, last day and after;
  - the admin reset: the action log entry, the event, the email, and that a customer admin
    can't reset another customer's user; platform staff resetting a bank's only administrator
    as a protected change;
  - the dual-control flow for the setting.
  - **Manual pass:** turning it on for a bank with users who have no key, the banner, the
    forced registration, a reset, and an SSO user unaffected.
- **OW4:** ZAP's job runs every time; the CRS mode runs in the suite against a stub, the same
  way `edge_check.py` already does.
- Each stage runs the three suites once (`scripts/run_tests.sh sqlite|postgres|linux`).

## Progress log

- 2026-10-17: First draft written from a survey of the guide (all 11 sections) and the
  codebase.
- 2026-10-17: Plan reviewed question by question (Q1–Q22). Changes from the first draft:
  - the "composition rules by default" gap removed, since the defaults don't require them;
  - ASVS 6.3.3 (required MFA) found as a gap and planned as OW3b, with admin reset, a grace
    period and exemptions;
  - passwords re-checked at sign-in;
  - gaps kept in private advisories until fixed, and reports taken through GitHub;
  - lock files built with uv and binding every installer;
  - SAMM cut to six practices;
  - metrics moved to the release notes;
  - OW3 split into OW3a (1.22.0) and OW3b (1.23.0).
- 2026-10-17: Domain modeling. `CONTEXT.md` started, and ADRs 0001–0004 written. The plan now
  says service provider, platform staff, embedding host and security key, as `CONTEXT.md`
  does. OW-D16: the minimum length is worked out from the bank's MFA setting only.
  OW-D21: platform staff can reset a bank's only administrator.
- 2026-10-17: OW1 under way. Private vulnerability reporting turned on. ASVS assessment of V6–V11
  and V16 done (working notes in `security-findings/`, ADR 0002): four private advisories,
  issues #1–#10 labelled `security`. The one high-severity finding (switching organization
  skipped the target's sign-on rules) was fixed and published in 1.21.2. To decide in OW3b:
  adaptive controls (time, place, device), checking that SSO users' identity provider used
  MFA, tokens bound to their client, logging reads of sensitive data.
- 2026-10-17: ASVS assessment finished: all 16 chapters in scope (V1–V16, 283 requirements).
  The last batch (V1–V5, V12–V15) found nothing high-severity; issues #11–#15. One more
  decision for OW3b: malware scanning of uploads. OW1 still to do: risk profile, threat
  model, SAMM, and building `docs/compliance/asvs.md` from the working notes.
- 2026-10-17: OW1 documents: `docs/security/risk-profile.md` (data classification and protection
  by level, ASVS 14.1), the threat model as code (`threat_model/openclear.py`, rendered by
  `scripts/render_threat_model.py`, 22 OpenClear-specific threats plus pytm's library and a
  LINDDUN GO pass; `tests/test_compliance/test_threat_model.py` fails when it's stale), and
  `docs/security/samm.md`. Found while writing them: account numbers aren't masked anywhere
  (11.7.2, with #14), and there's no in-app offboarding of a departing bank's data. Left in OW1:
  `docs/compliance/asvs.md`, published after OW3 (ADR 0002).
- 2026-10-17: OW2 built. Hashed locks (`scripts/lock.sh`, uv, universal for Python 3.11+): the
  image, the launcher, the Linux suite, CI and the docs site install only from them. Gates in
  `.github/workflows/security.yml` (pip-audit with expiring waivers, a stale-lock check, Bandit
  high, gitleaks) and `codeql.yml` (Python, JS/TS, C#); Actions pinned to SHAs, `permissions:`
  everywhere, base images by digest; SBOM on release (`scripts/sbom.py`, with the vendored JS
  manifest). Deviations: the image now carries every runtime extra without system libraries, so
  one locked image works everywhere (`EXTRAS` adds only mssql/opencv, unlocked); Dependabot
  can't update uv's lock files, so `lock-refresh.yml` opens the weekly Python PR instead
  (Dependabot covers Actions, Docker and the SDKs). Full-history gitleaks scan: 91 hits, none
  real (the public dev keys, library files in a Drive folder committed in Billing B1–B3, test
  fixtures); allowlisted in `.gitleaks.toml` with reasons, nothing to rotate.
- 2026-10-18: OW3a built on `ow3a`. Breached passwords (bundled NCSC top 100,000 as SHA-1
  prefixes; optional HIBP download file searched in place, or the range API) and context words;
  15 characters for new banks (OW-D16 simplified: stored, not worked out, until OW3b adds the
  MFA setting it would depend on); passwords re-checked at sign-in. The three private
  advisories: sign-in spends a password check for unknown and locked accounts and answers them
  alike (F-4; API 423 → 401); an administrator's reset emails a one-time link (F-3,
  `password_reset` table with RLS; refused without email); key changes need a sign-in within 10
  minutes, are audited and emailed (F-2; new `auth` session claim). `security.txt` and
  `SECURITY.md`. Found on the way: a broad `data/` rule in .gitignore hid the bundled list;
  the security-headers middleware overwrote pages' `no-referrer`.
- 2026-10-18: 1.22.0 released (OW1 docs, OW2, OW3a); the three advisories published. Then the
  `ready-for-agent` issues for 1.22.1: #1 (pre-hash before bcrypt), #6 (SSO by subject), #7
  (RSA-3072 for new SAML keys; re-keying existing ones is #21), #8 (token iss/aud), #9 (text
  log escaping), #11 (cookie prefixes), #12 (API docs pages off in production), #13
  (Clear-Site-Data), #20 (SIEM TLS). #2 was done in 1.22.0.

  Not started.
- 2026-10-18: OW3b and onward reviewed question by question (Q1–Q21). Decisions OW-D23–D35:
  platform staff keys in their own table; password length stays stored, with a hint; an
  optional SSO MFA check; key-pair-only API clients; adaptive controls and full read logging
  recorded as not met at Level 3; a sessions table, new-device emails and changeable email
  addresses (the UUID is the person; the user found the gap from a company rename); bank email
  domains and a domain move; ClamAV and VirusTotal (hash-only) hooks with a malware CUEC;
  account numbers out of URLs. Split into 1.23.0 (required MFA), 1.24.0 (sessions and identity)
  and 1.25.0 (files and verification). ADRs 0005–0007.
- 2026-10-18: OW3b built on `ow3b`: required security keys (bank and customer, grace period,
  banner, dual control, `security_key_required` check), the admin key reset with an identity note
  and the platform reset of a bank's only admin, platform staff keys (own tables, always
  required), the SSO MFA check, key-pair-only API clients, the password hint, the 1.22.1
  fallbacks removed, CUECs B-IA-2(2)-02/-03, the security documentation (#3, #10, #15) and
  `docs/compliance/asvs.md` with `scripts/asvs_crosswalk.py`.
- 2026-10-18: OW3c built on `ow3c`: the sessions table and its pages, signing a person or
  everyone out (OW-D29; ASVS 7.4.5, 7.5.2 now Met); new-device emails by a hashed `__Host-device`
  cookie, the first browser quiet (OW-D30; 6.3.5 Met); email changes by the person and
  administrators (a link to the new address), SCIM and SSO (a verified domain, nobody else with
  it, no memberships elsewhere), one `apply_change` emailing both addresses and recording
  `user.email.changed` (OW-D31); bank email domains on the SSO page (OW-D32); "Move everyone
  to a new domain" as an always-approved protected change, with a confirmation link for people
  who belong elsewhere (OW-D33). Found on the way: demo and sandbox wipes didn't delete rows
  keyed only by person (now `demo_tenant_service.delete_users`).
- 2026-10-18: 1.24.0 released (OW3c).
- 2026-10-19: OW4 built on `ow4`: malware scanning (OW-D34) as an app-wide FastAPI dependency
  over every multipart upload plus the drop folder, ClamAV by INSTREAM and VirusTotal by hash,
  an hourly re-check that quarantines, CUEC B-SI-3-01; account numbers out of URLs (OW-D35, #14:
  list searches posted to `/ui/list-search`, which redirects with account ids; header search
  posted; the edge guide drops query strings from logs); check-image views logged once an hour
  (OW-D28); ZAP baseline in CI and a weekly active scan (`scripts/zap_scan.py`,
  `zap-rules.tsv`), signed in as a bank admin and a customer user of a seeded bank; the CRS
  exclusions in `docs/guides/crs/` proven at paranoia 1 and 2 by `edge_check.py --crs` (the
  official ModSecurity image rather than Coraza: the same CRS, and a maintained container); the
  WSTG checklist in the PR template. The first ZAP runs (local active, sandbox passive) found
  the license page's 500 in the Docker image, and a CSRF token ZAP couldn't see through `form=`;
  `style-src 'unsafe-inline'` waived with its reason. ASVS 14.2.1 Met; 5.4.3 evidence added.
- 2026-10-19: 1.25.0 released (OW4). OW5 built on `ow5`: `scripts/release_metrics.py` (ASVS
  coverage, advisories fixed, dependency age, SBOM) writing `docs/compliance/security-metrics.md`
  for the TPRM pack and a section for each release's notes, in CLAUDE.md's release steps; the PR
  template's asvs-mark and new-dependency lines, with the CLAUDE.md convention; "Writing a safe
  plugin" in `docs/PLUGINS.md`; the penetration test and the SAMM re-score (by 2027-10-17) on
  MASTER_PLAN.md's waiting list; the TPRM pack updated (testing, ASVS, metrics, SECURITY.md,
  malware scanning). The plan is complete.
