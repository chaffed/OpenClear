# Security logging and SIEM integration

A bank's security operations team watches its systems from a SIEM: Splunk, Microsoft
Sentinel, IBM QRadar, Elastic, or a syslog collector feeding one. Examiners expect a bank to
log security-relevant events from the applications it relies on, keep them, and review them
(FFIEC *Information Security*, "Security operations" and "Log management"; Nacha's data
security requirements). This plan makes OpenClear a good source for that: the right events,
in a structured form, delivered reliably to whatever the bank uses.

It builds on two other plans:
- WAF_PLAN.md W1 (the real client IP behind an edge) and W2 (request ids, and the source IP
  in the action log);
- COMPLIANCE_PLAN.md (the action log, segregation of duties, Controls, the evidence export).

## What exists today

| Area | Today |
|---|---|
| Process logs | Python `logging` to stdout as plain text (`%(asctime)s %(levelname)s %(name)s: %(message)s` in the worker), and uvicorn's access lines. About 20 modules log warnings and errors. A container log collector can pick these up, but there's no JSON and no bank, user, request or event fields to search on. |
| Action log | Every state-changing action, by person or system, with time, channel, actor, resource and details. Hash-chained and ECDSA-signed, per bank, in the database. Seen at Admin → Action log, or in the signed evidence export for a period. **There's no feed a SIEM can follow.** |
| Sign-in events | SSO and embedded sign-ins are in the action log (`user.login_sso`, `user.login_embed`). These are logged **nowhere**: password sign-ins, failed sign-ins, lockouts (all decided in one place, `auth/login_service.authenticate_password`), WebAuthn failures, OAuth token failures, and rate-limit refusals. |
| Outbound events | Webhooks, and notification-channel plugins, carry business events only (exceptions, uploads, decision files). |
| Source IP | Used for rate limits and recorded on a WSUD. **Not** in the action log. |

## Two streams

The design separates two kinds of record, because they need different handling.

1. **Operational logs:** requests, warnings, errors and job activity. They're high volume,
   and losing a few lines in an outage is tolerable. They go to stdout as JSON, optionally
   also straight to syslog. They're never written to the database.
2. **Security events:** sign-ins, failures, lockouts, permission and group changes,
   segregation-of-duties breaches, key and settings changes, exports, and control failures.
   They're low volume, and each one matters. Each is written durably to an outbox **in the
   same transaction as the action**, then forwarded to each destination with at-least-once
   delivery. They can also be pulled through an API. Events that belong to a bank are also
   action-log entries, so they stay tamper-evident.

## Phases

| Phase | What | Notes |
|---|---|---|
| S1 | **Structured logging.** `OPENCLEAR_LOG_FORMAT=json` (the default stays `text`). One JSON object per line on stdout:<br>• the time (UTC, ISO 8601, milliseconds), level, logger and message;<br>• the request's id (WAF_PLAN W2), method, path, status and duration;<br>• the client IP (W1), the bank's slug, the user or API client id;<br>• the worker or job id for background work.<br>Request fields come from a context variable set by middleware, so every log line in a request carries them without changing call sites. uvicorn's access log moves into the same format. Request bodies, tokens, cookies and query strings are **never logged**; query strings may carry codes and secrets (the embed launch code, OAuth). One logging setup serves the app, the worker and the launcher. | With this alone, the usual container route works well: Splunk OpenTelemetry Collector, Fluent Bit, the Datadog agent, CloudWatch, Azure Monitor. |
| S2 | **Security events.** A catalog with a stable name, a category and a severity for each event (below). They're emitted through one function, `security_events.emit(...)`, which writes:<br>• the action-log entry (when the event belongs to a bank);<br>• the outbox row;<br>• a log line.<br>Hooks to add: `authenticate_password`'s outcomes (success, bad credentials, locked, SSO required); WebAuthn and MFA results; the OAuth token endpoint; token-exchange and act-as refusals; SCIM authentication failures; session revocation and sign-out; rate-limit refusals (sampled, so a flood becomes one event a minute per source, with a count); `sod.violation`; and the actions already in the action log that matter to security (users, groups, memberships, API clients, SSO connections, webhooks, plugins, settings, key rotation, exports, model and rule changes). | A failed sign-in for an unknown bank or email has no bank to belong to. It goes to the installation-wide stream only, and never says whether the account exists. |
| S3 | **Shipping.** Destinations, each with its own cursor and retries through the job queue (backing off like webhooks), so a SIEM outage never slows or fails a request:<br>• **syslog**, RFC 5424 over TCP with TLS (RFC 5425) or plain TCP/UDP for a local relay, with the message as JSON, CEF (ArcSight, QRadar) or LEEF (QRadar);<br>• **Splunk HTTP Event Collector**, with token, index, source type and acknowledgement;<br>• **generic HTTPS JSON**: Elastic, Microsoft Sentinel through the Logs Ingestion API (an Entra app), Datadog, Sumo Logic, or a bank's own collector.<br>Destinations exist at two levels:<br>• **the installation's**, set by the operator in configuration, which receives every bank's events and the operational logs if asked;<br>• **each bank's**, set by the bank in Settings → Security event forwarding, which receives only that bank's events. A hosted OpenClear serves many banks, and each bank's SOC wants its own.<br>Bank destinations go through the same outbound-address checks as SSO and webhooks (`auth/outbound_http.py`: public addresses only). Their secrets are stored AES-256-GCM (`auth/crypto.py`). There's a "Send test event" button. | Operational logs can also go to syslog directly, through a non-blocking logging handler. Lines are dropped when the queue is full, never blocked on, and the drops are counted. |
| S4 | **Pull API.** `GET /api/v1/security-events?after=<id>&limit=` gives a bank's security events in order, with a cursor, like the decision feed. `GET /api/v1/audit-log?after_sequence=` gives the action log itself, each entry with its hash, previous hash and signature, so a bank's SIEM or archive can verify the chain independently (with the public key from the evidence export). Gated on `audit_log:read`; API clients welcome. The SDK gains `openclear events tail`, which follows the feed and prints JSON lines for piping into a collector. A **Splunk** example (a modular input or a scripted input using the SDK) and a **Sentinel** example (a Logic App or data connector polling the API) are included. | Pull suits banks whose SIEM can't receive from outside, or that want to verify the chain. |
| S5 | **Controls, docs and evidence.**<br>• A new control, "Security events delivered": **FAIL** if a destination has been failing for over an hour or its backlog is growing; **WARN** if no destination is configured and the feed hasn't been read in a day; **PASS** with each destination's last success and backlog.<br>• The evidence export gains the security-event counts for the period by category, and each destination's delivery record.<br>• A guide, `docs/guides/siem.md`, with configuration for Splunk, Sentinel, QRadar, Elastic and a syslog relay. It includes suggested detections: repeated failed sign-ins, a lockout followed by a success from a new IP, an admin granting themselves permissions, a group combining duties, a key rotation, a large export, and a control turning to FAIL.<br>• A row on log management in `docs/compliance/ffiec.md`. | |

## Waves

The phases are built in **two waves**. **Each wave is one merge to `main`:** one run of the
three full suites and one manual check. Within a wave, commits run only the tests they
touch. MASTER_PLAN.md places these waves among the WAF and UX waves.

| Wave | Phases | Contents | Tested by |
|---|---|---|---|
| **A. Events** | S1, S2, S4 | • JSON logging, built on the request context from WAF wave A.<br>• The security event catalog, the emitter, the outbox table and the hooks.<br>• The pull APIs for security events and the action log, and `openclear events tail`.<br>Pull needs no destinations, so a bank can collect events from the first release. | Unit tests per hook; the no-secrets test; isolation between banks on Postgres. A manual sign-in session (good and bad passwords, a lockout, MFA, a failed token) checked against the feed and the JSON logs. |
| **B. Shipping** | S3, S5 | • Syslog, Splunk HEC and HTTPS destinations: the installation's from configuration, and each bank's as a tile in the control panel (UX wave 2).<br>• The "Security events delivered" control; counts in the evidence export.<br>• The guide, and the crosswalk row. | Fake receivers in the suite (TLS syslog, HEC, HTTPS) for outages, retries and catch-up. Once, against a real Splunk container and a syslog-ng relay with TLS. |

**Why not ship in wave A too?** A bank's destinations are a settings screen. Built before
the control panel, it would be one more card to rework. The installation's own destinations
need no screen, but they share the delivery code with the banks' destinations. Building both
at once tests that code once.

## The event record

One schema for every destination and format. The JSON form is close to Elastic Common
Schema field names, so it maps onto ECS, Splunk CIM and OCSF with little work; the guide
gives the mapping for each.

```json
{
  "id": "evt_01J…",                     // unique, ordered; the feed's cursor
  "time": "2026-10-09T14:03:22.118Z",
  "event": {"name": "auth.login.failed", "category": "authentication", "outcome": "failure",
            "severity": "medium", "reason": "invalid_credentials"},
  "bank": {"id": "…", "slug": "acme-bank"},          // absent for installation-wide events
  "actor": {"type": "user", "id": "…", "email": "a@acme-bank.example"},   // or api_client, system
  "target": {"type": "security_group", "id": "…", "name": "Approver"},
  "source": {"ip": "203.0.113.7", "user_agent": "…"},
  "request": {"id": "8c2f…", "edge_id": "84a1…-IAD", "channel": "web"},
  "audit": {"sequence": 1234, "entry_hash": "…"},    // when it's also an action-log entry
  "details": {"attempts": 3}
}
```

- **CEF:** `CEF:0|OpenClear|OpenClear|<version>|<event name>|<summary>|<severity 0-10>|`
  followed by the fields as standard CEF keys (`src`, `suser`, `duser`, `outcome`, `act`,
  `cs1`, the bank's slug, and so on).
- **LEEF:** `LEEF:2.0|OpenClear|OpenClear|<version>|<event name>|` followed by the same fields.

### What's never in an event

- passwords, tokens, secrets, session ids;
- the content of uploaded files;
- account numbers, amounts and payees.

An event says *that* something was done to a resource, by whom and from where. The details
stay in OpenClear, behind its access controls. That keeps the SIEM out of scope for the
data security requirements that apply to account data.

## The catalog (first version)

| Category | Events |
|---|---|
| authentication | `auth.login.succeeded`, `auth.login.failed` (reason: `invalid_credentials`, `locked`, `sso_required`, `inactive`), `auth.lockout`, `auth.mfa.succeeded`, `auth.mfa.failed`, `auth.sso.succeeded`, `auth.sso.failed`, `auth.logout`, `auth.session.revoked`, `auth.token.issued`, `auth.token.failed` (API clients), `auth.token_exchange.refused`, `auth.act_as.refused`, `scim.auth.failed`, `embed.launch.refused` |
| access | `access.denied` (a 403 on a permission check: sampled per person, one a minute, with a count), `rate_limit.exceeded` (sampled per source), `sod.violation` |
| identity | `user.created`, `user.deactivated`, `user.reactivated`, `membership.changed`, `security_group.created`, `security_group.changed`, `security_group.deleted`, `webauthn.credential.added`, `webauthn.credential.removed`, `scim.user.provisioned`, `scim.user.deprovisioned` |
| integration | `api_client.created`, `api_client.secret_rotated`, `api_client.revoked`, `sso_connection.changed`, `webhook.changed`, `embed.origins.changed`, `security_events.destination.changed` |
| configuration | `settings.changed` (with which setting), `sod.mode.changed`, `plugin.enabled`, `plugin.disabled`, `change_request.created`, `change_request.approved`, `change_request.rejected`, `ml_model.activated` |
| data | `audit.export`, `data_export.started`, `data_export.downloaded`, `users.exported`, `bulk_upload.reversed` |
| integrity | `audit.chain.verify_failed`, `control.status.changed` (any control moving to WARN or FAIL, and back), `anomaly_capture.failed`, `secrets.reencrypted`, `key.rotation.detected` |

## Decisions

| # | Question | Decision |
|---|---|---|
| SI-D1 | Ship operational logs through the database outbox too? | No. They're high volume and loss-tolerant: stdout as JSON for collectors, and a non-blocking syslog handler. Only security events are durable. |
| SI-D2 | Formats | JSON (the default), CEF and LEEF on syslog; JSON for Splunk HEC and HTTPS. One schema behind all of them. |
| SI-D3 | Per-bank destinations on a shared installation? | Yes, set by each bank, with the same outbound checks as webhooks. The installation's destination is the operator's and gets everything. |
| SI-D4 | Delivery guarantee | At least once, in order per destination, with the event `id` for de-duplication. Retries back off like webhooks. A destination that fails for too long stays in place, and catches up from its cursor when it recovers. The Controls page fails meanwhile. |
| SI-D5 | Is every security event also an action-log entry? | Those that belong to a bank and change something, yes. Failed attempts and refusals go to the outbox and logs only, so a password-guessing flood can't swell the signed chain. They're still durable, counted, and in the evidence export. |
| SI-D6 | Account data in events | Never (see "What's never in an event"). |
| SI-D7 | How long the outbox keeps events | Delivered events are cleared by housekeeping after `security_event_retention_days` (default 30). The SIEM is the long-term store; the action log keeps what belongs in it. |
| SI-D8 | Which waves? | A: events and pull (S1, S2, S4). B: shipping, controls and docs (S3, S5), after the control panel, so a bank's destinations are a tile. |

## Testing

- **Unit:**
  - each hook emits the right event, with no secrets or account data (a test feeds sentinel secrets through sign-in, token and SSO paths and checks no event or log line contains them);
  - CEF and LEEF escaping;
  - RFC 5424 framing;
  - sampling of floods;
  - JSON log lines carry request context.
- **Delivery:**
  - a local TLS syslog receiver, a fake HEC endpoint and a fake HTTPS collector in the suite;
  - outages, retries, ordering and catch-up;
  - nothing a request does waits on a destination.
- **Isolation:** one bank's events never reach another bank's destination or feed (with
  Postgres row-level security on the outbox table).
- **Against real tools, before release:** Splunk (the free container image) through HEC and
  syslog; Elastic through HTTPS; a syslog-ng relay with TLS. Sentinel and QRadar are checked
  against their documented formats, and against a live tenant if a bank or partner can
  lend one.

## Progress log

(Newest last.)
- 2026-10-09 Plan written from a review of the logging, the action log, the sign-in paths
  and the outbound integrations. Nothing built yet.
- 2026-10-10 Reorganised into two waves (A events and pull, B shipping) to cut test cycles.
  See MASTER_PLAN.md for the overall order.
