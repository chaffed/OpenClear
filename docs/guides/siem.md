# Sending security events to a SIEM

OpenClear records sign-ins and their failures, lockouts, second factors, API tokens, refusals,
and changes to who may do what as **security events**. This guide covers getting them into
Splunk, Microsoft Sentinel, IBM QRadar, Elastic, or a syslog collector, and what to alert
on. It's for a bank's security team and for whoever runs the OpenClear installation.

## Three ways to collect them

| Way | Who sets it up | What it receives |
|---|---|---|
| **A bank's own destination** | The bank, in **Settings → Security event forwarding** | That bank's events only |
| **The installation's destination** | The operator, in configuration (`OPENCLEAR_SIEM_*`) | Every bank's events, and those that belong to no bank (such as a failed sign-in naming a bank that doesn't exist) |
| **The pull API** | Anyone with `audit_log:read` | `GET /api/v1/security-events` (one bank), `GET /api/v1/platform/security-events` (the operator, every bank) |

A hosted OpenClear serves many banks, and each bank's SOC usually wants its own copy. That's
what a bank's destination is for. A bank running its own installation can use either.

## How delivery works

- **In order, at least once.** Each destination keeps a cursor: the id of the last event it
  accepted. Events go in batches (500 by default). A batch counts as delivered only once
  the destination accepts it, so after an outage some events may arrive twice. De-duplicate
  on the event's `id`.
- **A destination that's down never slows OpenClear.** Delivery runs in the background
  every 15 seconds (`OPENCLEAR_SECURITY_EVENT_DELIVERY_TICK_SECONDS`). After a failure it
  waits 5 seconds, then 30, 2 minutes, 5 minutes, and then every 15 minutes, for as long as
  it takes. When the destination recovers, it catches up from its cursor.
- **Nothing undelivered is cleared.** Events are kept for
  `OPENCLEAR_SECURITY_EVENT_RETENTION_DAYS` (30), but an event a destination hasn't
  received yet is kept until it has.
- **It's watched.** The control **Security events delivered** (Settings → Controls) fails
  when a destination has been failing for over an hour, or its oldest waiting event is
  more than an hour old. With no destination, it warns if nobody has read the pull API for a
  day. The evidence export includes each destination's delivery record and the period's
  event counts by category.
- **Turning it on.** The operator sets `OPENCLEAR_ENABLE_SECURITY_EVENT_DELIVERY_SCHEDULER=true`
  (the hosted sandbox has it on). Without it, events are still recorded and readable
  through the API.

One limit: an event id is assigned when the event is written, but the event becomes
visible when its transaction commits. Delivery leaves the last few seconds of events
(`OPENCLEAR_SIEM_SETTLE_SECONDS`, 5) until later so slower requests finish first. An event
from a transaction longer than that could be passed over by the cursor. It's still in the
pull API and, if it changed something, in the action log.

## What an event looks like

```json
{
  "id": "evt_1234", "sequence": 1234, "time": "2026-10-11T14:03:22.118000+00:00",
  "event": {"name": "auth.login.failed", "category": "authentication", "outcome": "failure",
            "severity": "medium", "reason": "invalid_credentials"},
  "bank": {"id": "…", "slug": "acme-bank"},
  "actor": {"type": "user", "id": "…", "email": "a@acme-bank.example"},
  "source": {"ip": "203.0.113.7", "user_agent": "…"},
  "request": {"id": "8c2f…", "channel": "web"},
  "details": {"attempts": 3}
}
```

Events never contain passwords, tokens, secrets, session ids, uploaded files, account
numbers, amounts or payees. An event says *that* something happened, by whom and from
where; the details stay in OpenClear. That keeps your SIEM out of scope for the data
security rules that apply to account data.

The categories are `authentication`, `access`, `identity`, `integration`, `configuration`,
`data` and `integrity`. The full catalogue is in `services/security_events.py`.

### Field mapping

| OpenClear | Elastic (ECS) | Splunk (CIM) | OCSF |
|---|---|---|---|
| `time` | `@timestamp` | `_time` | `time` |
| `event.name` | `event.action` | `signature` | `activity_name` |
| `event.category` | `event.category` | `action` (by data model) | `category_name` |
| `event.outcome` | `event.outcome` | `action` (`success` / `failure`) | `status` |
| `event.severity` | `event.severity` | `severity` | `severity` |
| `actor.email` | `user.email` | `user` | `actor.user.email_addr` |
| `actor.id` | `user.id` | `user_id` | `actor.user.uid` |
| `source.ip` | `source.ip` | `src` | `src_endpoint.ip` |
| `source.user_agent` | `user_agent.original` | `http_user_agent` | `http_request.user_agent` |
| `target.type`, `target.id` | `event.target`… (custom) | `object_category`, `object_id` | `resources[]` |
| `bank.slug` | `organization.name` | `vendor_account` | `cloud.account.name` |
| `request.id` | `trace.id` | `request_id` (custom) | `metadata.correlation_uid` |
| `id` | `event.id` | `event_id` | `metadata.uid` |

## Formats

- **JSON** (the default): the record above. Splunk HEC and HTTPS always use it.
- **CEF** on syslog (ArcSight, QRadar, Sentinel's CEF connector):
  `CEF:0|OpenClear|OpenClear|<version>|<event name>|<event name> (<outcome>)|<severity 0–10>|`
  then `rt`, `externalId`, `cat`, `outcome`, `reason`, `src`, `suser`, `suid`, `duid`,
  `duser`, and `cs1`–`cs6` with their labels: the bank, the actor type, the target type, the
  request id, the channel and the details as JSON.
- **LEEF 2.0** on syslog (QRadar): `LEEF:2.0|OpenClear|OpenClear|<version>|<event name>|x09|`
  then tab-separated attributes, the same fields as CEF plus `devTime`, `sev`, `usrName`
  and `identSrc`.

Syslog messages are RFC 5424, facility 13 (log audit), app name `openclear`, with the event
name as the message id. Over TLS they're octet-counted (RFC 5425); over plain TCP each ends
with a newline (RFC 6587's traditional framing, which Splunk's TCP input and QRadar expect).
Severity: low → informational, medium → notice, high → warning, critical → critical.

## Setting up a bank's destination

In **Settings → Security event forwarding**, choose where to send, fill in the fields, and
press **Save and send a test event**. The test event is `security_events.test`; it isn't
stored and isn't sent again. The tile shows whether delivery is working, and the window
shows the last delivery and how many events are waiting.

A bank's destination must be a public address. OpenClear refuses private and internal
addresses, the same rule as for webhooks and SSO, unless the operator allows them with
`OPENCLEAR_SIEM_ALLOW_PRIVATE_HOSTS` (for a single-bank installation on the bank's own
network). Tokens are stored encrypted (AES-256-GCM). Saving, changing or removing a
destination is recorded in the action log and as a `security_events.destination.changed`
event.

## Setting up the installation's destination

```sh
OPENCLEAR_ENABLE_SECURITY_EVENT_DELIVERY_SCHEDULER=true
OPENCLEAR_SIEM_KIND=syslog              # syslog, splunk_hec or https
OPENCLEAR_SIEM_HOST=siem.internal.example
OPENCLEAR_SIEM_PORT=6514
OPENCLEAR_SIEM_TRANSPORT=tls            # tls, tcp, or udp (only for a relay on the same host)
OPENCLEAR_SIEM_FORMAT=json              # json, cef or leef
OPENCLEAR_SIEM_TLS_CA_FILE=/etc/openclear/siem-ca.pem   # to trust a private CA (syslog, HEC or HTTPS)
# For Splunk HEC or HTTPS instead:
OPENCLEAR_SIEM_URL=https://splunk.internal.example:8088
OPENCLEAR_SIEM_TOKEN=…
OPENCLEAR_SIEM_SPLUNK_INDEX=openclear
OPENCLEAR_SIEM_SPLUNK_SOURCETYPE=openclear:security
```

The installation's destination may be on a private network. Removing the configuration
pauses it and keeps its cursor, so turning it back on resumes where it stopped.

## Splunk

**HTTP Event Collector** (recommended):

1. In Splunk, **Settings → Data inputs → HTTP Event Collector → New Token**. Choose an
   index (for example `openclear`) and, if you want delivery confirmed only once indexed,
   turn on **Enable indexer acknowledgement**.
2. In OpenClear, choose **Splunk HTTP Event Collector**. The URL is
   `https://<splunk>:8088` (OpenClear adds `/services/collector/event`). Paste the token.
   Index and source type are optional; the source type defaults to `openclear:security`.

With acknowledgement on, OpenClear sends each batch with a request channel and waits for
Splunk to confirm it was indexed before moving on. Splunk stops indexing, and so stops
acknowledging, when its disk has less free space than `minFreeSpace` (5 GB by default);
delivery then retries until it has room.

**Syslog**: over TLS, send to Splunk Connect for Syslog (SC4S) or a relay (below), which
read RFC 5425's octet counting; Splunk's own TCP input doesn't, and would run messages
together. Over plain TCP on a trusted network, Splunk's TCP input works directly (one message
per line). With JSON format, set `KV_MODE = json` for the source type.

Search: `index=openclear sourcetype="openclear:security"`. Splunk extracts the JSON fields
itself (`event.name`, `actor.email`, `source.ip`…); there's no need for `spath`.

## Microsoft Sentinel

Two ways:

- **Logs Ingestion API** (recommended). Create a data collection endpoint and rule with a
  custom table (for example `OpenClear_CL`) whose columns follow the record above, and an
  Entra app with **Monitoring Metrics Publisher** on the rule. A small relay (an Azure
  Function or Logic App) receives OpenClear's HTTPS POSTs, a JSON array of records with
  `Authorization: Bearer <token>`, and forwards them with the app's credentials. OpenClear
  sends one static token rather than signing in to Entra itself.
- **CEF over syslog**, through the Azure Monitor Agent on a Linux forwarder with the
  **Common Event Format** connector. Events land in `CommonSecurityLog`, with
  `DeviceVendor == "OpenClear"`.

## IBM QRadar

Send **LEEF** (or CEF) over syslog TLS to a QRadar event collector, port 6514, and add a log
source of type **Universal LEEF** (or **Universal CEF**) with the sending host as its
identifier. A custom DSM can map `event.name` (the LEEF event id) to QRadar event names;
the category and outcome are in `cat` and `outcome`.

## Elastic

Send **HTTPS JSON** to an ingest endpoint you control, such as a Logstash `http` input or
an Elastic Agent **Custom HTTP Endpoint** integration, with the token as a bearer token.
Use an ingest pipeline to rename fields to ECS (see the field mapping above) and set
`@timestamp` from `time`. Each POST body is a JSON array; split it into one document per
event (Logstash `split`, or the integration's `split` option).

## A syslog relay (syslog-ng, rsyslog)

A relay on the bank's network can receive TLS syslog from OpenClear and pass it to
whatever the bank uses. A minimal syslog-ng source:

```
source s_openclear {
  syslog(
    transport("tls") port(6514)
    tls(key-file("/etc/syslog-ng/tls/server.key") cert-file("/etc/syslog-ng/tls/server.crt")
        peer-verify(optional-untrusted))
  );
};
destination d_openclear { file("/var/log/openclear/events.log" template("$ISODATE $HOST $PROGRAM $MSGID $MSG\n")); };
log { source(s_openclear); destination(d_openclear); };
```

Use the `syslog()` source, which reads RFC 5424 with octet counting (RFC 5425); `network()`
expects newline-separated legacy syslog and would run messages together. For rsyslog, use
`imtcp` with `StreamDriver.Mode="1"` and the `gtls` driver.

## What to alert on

Suggested detections, as queries in plain terms. Adjust the thresholds to your bank.

| Detection | Events | Rule |
|---|---|---|
| Password guessing | `auth.login.failed` | 10 or more failures for one `actor.email`, or from one `source.ip`, within 10 minutes |
| A lockout, then a sign-in from somewhere new | `auth.lockout`, then `auth.login.succeeded` | The same person signs in within a day of a lockout from a `source.ip` not seen for them in 30 days |
| An admin granting themselves more | `security_group.changed`, `membership.changed` | `actor.id` equals the affected user, or the actor changes the group they're in |
| A group that combines duties | `sod.violation`, `security_group.changed` | Any `sod.violation`; any `security_group.changed` whose details name conflicting permissions |
| A key rotation | `secrets.reencrypted` (stored secrets moved to a new key at startup) | Any occurrence outside a planned change window |
| A large export | `data_export.started`, `audit.export`, `list.exported`, `users.exported` | Any full data export; more than 5 list exports by one person in an hour |
| A control turning to FAIL | Not an event yet | Poll `GET /api/v1/controls/status` from your monitoring, or run `openclear controls`, which exits non-zero on a FAIL |
| A new way in | `api_client.created`, `sso_connection.changed`, `security_events.destination.changed`, `webhook.changed` | Any occurrence, for review |
| Token abuse | `auth.token.failed`, `rate_limit.exceeded` | A burst from one client or address (these are sampled to one event a minute per source; read `details.since_last_event` for the count) |

## Checking it end to end

1. Save the destination and press **Save and send a test event**. Look for
   `security_events.test` in your SIEM.
2. Sign in to OpenClear with a wrong password. Within a minute, `auth.login.failed` should
   arrive with your address and `reason: invalid_credentials`.
3. Open **Settings → Controls**: **Security events delivered** should pass, with the last
   delivery time.
