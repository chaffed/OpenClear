# Third-party risk management

The 2023 *Interagency Guidance on Third-Party Relationships: Risk Management* (Federal
Reserve, FDIC and OCC) asks a bank to manage each third-party relationship through its life
cycle:
1. planning;
2. due diligence and selection;
3. contract negotiation;
4. ongoing monitoring;
5. termination.

This page helps with OpenClear's part of that. It isn't legal advice ([Compliance](README.md)).

**Who the third party is depends on how OpenClear runs.**
- When the bank hosts it, OpenClear is open-source software (AGPL-3.0) the bank runs itself.
  The relationships to assess are with whoever hosts it, and with any optional services the
  bank turns on (below).
- When someone hosts OpenClear for the bank, that host is a third-party service provider, and
  this pack covers the software they run.

## Due-diligence pack

Answers the bank can use in its own due-diligence questionnaire. Each points to where to
verify it.

### Strategy, operations and the software

| Question | Answer |
|---|---|
| What it does | Positive pay for checks and ACH. It matches presented items against what the bank's customers issued or authorized, raises exceptions, and records pay/return decisions with optional machine-learning recommendations. |
| Source and licence | Open source, AGPL-3.0. The full source, history and tests are public. |
| Releases and changes | Versioned releases with a changelog, database migrations that upgrade and downgrade, and an API that changes only additively. |
| Testing before release | About 2,000 automated tests on SQLite, Postgres and Linux; CodeQL, Bandit, gitleaks, `pip-audit` and an OWASP ZAP baseline scan on every change; a weekly ZAP active scan. |
| Security review | Measured against OWASP ASVS 5.0 ([crosswalk](asvs.md)); a [threat model](../security/threat-model.md); `SECURITY_REVIEW.md` and published advisories with their fixes. An independent penetration test is planned. |
| Security metrics | [Each release's](security-metrics.md): ASVS coverage, advisories fixed, dependency age, and its SBOM (CycloneDX). |
| Vulnerability reports | [SECURITY.md](../../SECURITY.md): private reporting, and fix time frames by severity. |

### Information security

| Question | Answer |
|---|---|
| Access control | Fine-grained, per-bank security groups; customer scoping; segregation of duties with *warn* and *enforce* modes; dual control on decisions. |
| Authentication | Passwords (bcrypt, per-bank policy), FIDO2/WebAuthn, SSO (OIDC, SAML 2.0), SCIM; OAuth 2.0 client credentials for systems. |
| Encryption | HTTPS in transit. Secrets in the database: AES-256-GCM, versioned and rotatable. Disk and database encryption: the host's job, recorded on the Controls page. |
| Tenant isolation | Postgres row-level security per bank. |
| Logging | A hash-chained, ECDSA-signed action log of every state-changing action, verifiable at any time. |
| Monitoring | Live controls (PASS / WARN / FAIL) by page and API. |

### Data and subcontractors

| Question | Answer |
|---|---|
| Data held | Accounts and customers; issued checks, presented items and check images; ACH transactions and authorizations; exceptions and decisions; users and memberships; the action log. |
| Where it's held | Wherever the bank or its host runs the database and file storage. OpenClear sends nothing to its authors. |
| Optional outside services | Each is off unless configured: email and SMS providers for notifications; cloud OCR for check images; S3-compatible storage; identity providers for SSO; webhooks to the bank's own systems; malware scanning (ClamAV, and VirusTotal, which is sent only file hashes). Each one the bank turns on is a relationship to assess ([all of them](../security/data-and-limits.md#outside-connections)). |
| Model data sharing | None. Fraud scoring is off until the bank turns it on, and a bank's model learns only from its own decisions. No data is pooled across banks. |

### Resilience, incidents and exit

| Question | Answer |
|---|---|
| Resilience | Runs as several instances on one Postgres database. Background jobs retry, and a scheduled job runs on one instance at a time. Backups and failover are the host's. |
| Incident evidence | The action log and the evidence export reconstruct who did what, and when, for any period. |
| Termination and exit | A full export of the bank's data (Settings → Data export), and a database the bank holds itself when it self-hosts. |

## Ongoing monitoring

- Watch the Controls page, or poll `GET /api/v1/controls/status` from the bank's monitoring
  tools.
- Turn on the daily anomaly-capture test.
- Take an evidence export each quarter, or each exam cycle, and keep it with the bank's
  third-party file. It is signed, so it can be checked later.
- Review the changelog, and the release's security metrics, before each upgrade.
