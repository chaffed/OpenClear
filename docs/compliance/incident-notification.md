# Incident notification

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

Three rules decide who hears about an incident involving OpenClear, and when. This isn't legal
advice ([Compliance](README.md)).

| Rule | Who tells whom | When |
|---|---|---|
| 12 CFR 304.24 (FDIC) | OpenClear's host, as a bank service provider, tells at least one contact the bank designated, or else its CEO and CIO | As soon as possible after determining an incident that has disrupted or degraded the service, or is reasonably likely to, for four hours or more. Scheduled maintenance announced beforehand doesn't count |
| 12 CFR 304.23 (FDIC) | The bank tells its FDIC supervisory office | As soon as possible, and within 36 hours of determining a notification incident |
| 23 NYCRR 500.11(b)(3) (New York banks) | The host tells the bank, as the contract says | On a cybersecurity event directly affecting the bank's systems or data; DFS's 2025 guidance asks for "immediate or timely" notice |
| 23 NYCRR 500.17(a) (New York banks) | The bank tells DFS | Within 72 hours of determining a cybersecurity incident at the bank or at its service provider |
| Bank Service Company Act § 7(c) | The bank tells the FDIC the service relationship exists | Within 30 days of the contract or the first service |

## Designating contacts

An administrator names the bank's incident contacts in Settings → Incident contacts, with the CEO
and CIO as the fallback the rule gives, and confirms them each year. The control check *Incident
contacts named* (control OC-IR-02) warns while there are none, while a notice is unacknowledged,
or once a year has passed. Changing them is a high security event, `incident_contact.changed`:
whoever sets them decides where notices go.

## How the host notifies

From Platform → Incidents the installation's operator sends a notice to each bank affected
(control OC-IR-01):
- it's **emailed** at once to the bank's incident contacts (or its CEO and CIO, or, with neither,
  its administrators), whatever anyone's notification settings;
- it **shows to the bank's administrators** on every page until one acknowledges it;
- sending and acknowledging are in the bank's **action log** and raised as security events,
  `incident.notice.sent` (critical) and `incident.notice.acknowledged`, so they reach the bank's
  SIEM; the evidence export lists them;
- updates and closure follow the first notice the same way; scheduled maintenance is announced the
  same way, beforehand.

A notice doesn't wait for a second operator: speed comes first, and the platform's own action log
records who sent it. The [runbook](../../RUNBOOK.md#incidents-for-the-service-provider) has the steps.

## The bank's part

| CUEC | |
|---|---|
| B-IR-6-05 | Name incident contacts, and confirm them each year |
| B-IR-6-06 | Decide whether it's a notification incident; notify the FDIC within 36 hours |
| B-IR-6-07 | New York banks: notify DFS within 72 hours |
| B-SA-9-03 | Tell the FDIC about the service relationship within 30 days |
| B-IR-6-01 | Respond to an incident that involves OpenClear |

The host's part is H-IR-6-01 (notify banks), H-IR-8-01 (an incident response plan) and H-CM-4-01
(announce maintenance).

## Sources

Read 22 October 2026:
- 12 CFR Part 304 Subpart C, 304.21–304.24 (eCFR, current to 21 October 2026; unchanged since the
  rule's compliance date, 1 April 2022).
- 23 NYCRR Part 500 as amended in November 2023 (DFS's own compiled text, which it marks as not the
  official version), and DFS's industry letter of 21 October 2025 on third-party service providers.
- 12 U.S.C. 1867(c), the Bank Service Company Act § 7(c) (US Code, 2024 edition).

**Unconfirmed:** the FDIC's form and channel for the § 7(c) notice; whether a hosted positive pay
service is a § 7(c) "covered service" (likely, as data processing; for counsel).
