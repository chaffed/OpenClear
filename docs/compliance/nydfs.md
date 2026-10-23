# New York DFS Part 500

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

New York's cybersecurity regulation, 23 NYCRR Part 500, binds banks licensed under the New York
Banking Law. It doesn't bind OpenClear. But section 500.11 makes each bank set what its third-party
service providers must do, and **OpenClear aims to meet everything a bank can ask of one there**, and
to give a New York bank the evidence for each section that touches positive pay. Other states' rules
are evaluated separately. This isn't legal advice ([Compliance](README.md)).

**Who's the service provider?** Whoever hosts OpenClear for the bank. A bank that hosts it itself
has no service provider for OpenClear; to that bank, OpenClear is an externally developed
application (500.8).

## What a bank can require of its service provider (500.11)

| 500.11(b) asks for | What OpenClear does | Evidence |
|---|---|---|
| Access controls, including multi-factor authentication per 500.12 | Permissions on every request; security keys and passkeys, or the bank's identity provider; platform staff reach a bank's data only through a grant the bank makes; two people at the provider for provider changes | OC-AC-01, OC-IA-02, OC-IA-03, OC-AC-08, OC-AC-13; H-AC-6(5)-01 |
| Encryption in transit and at rest, per 500.15 | HTTPS with HSTS; secrets encrypted with AES-256-GCM; the host's statement of encryption at rest on the Controls page | OC-SC-01, OC-SC-02, OC-SC-04; H-SC-8(1)-01, H-SC-28(1)-01 |
| Notice of a cybersecurity event directly affecting the bank's systems or data | Incident notices to the bank's designated contacts, by email and in the app, recorded with the bank's acknowledgement ([Incident notification](incident-notification.md)) | OC-IR-01, OC-IR-02; H-IR-6-01 |
| Representations and warranties | The controls, CUECs, host attestations and each release's security metrics a contract can rely on | [Controls](controls.md), [CUECs](cuecs.md) |

## Section by section

| § | What it asks of the bank | OpenClear |
|---|---|---|
| 500.2, 500.3 | A cybersecurity program and written policies, approved yearly | The bank's (B-RA-3-01, B-PL-1-01); OpenClear's controls, evidence export and CSF crosswalk are its parts |
| 500.4 | A CISO, board reporting | The bank's alone |
| 500.5 | Penetration testing, vulnerability scanning and remediation | The host's yearly penetration test (H-CA-8-01) and patching (H-SI-2-01, H-RA-5-01); OpenClear's advisories and fix times (SECURITY.md). An independent test of OpenClear itself is planned (`plans/PARKED_PLAN.md`) |
| 500.6 | Records to reconstruct material transactions (five years) and audit trails (three) | A signed, hash-chained action log and every decision with who and when (OC-AU-01 to OC-AU-05). Security events are kept 30 days by default, so the three years come from the bank's SIEM (B-AU-6-01, B-AU-11-01) |
| 500.7 | Least privilege, yearly access reviews, ending access promptly, a password policy | Supported: OC-AC-01, OC-AC-05, OC-AC-07, OC-AC-02, access reviews, SCIM, the bank's password policy with breached passwords refused (OC-IA-01) |
| 500.8 | Secure development; evaluating externally developed applications | The ASVS 5.0 crosswalk, threat model, SAMM, SBOM and advisories; the bank evaluates them yearly (B-SA-11-01) |
| 500.9 | A risk assessment, yearly and on change | The bank's (B-RA-3-01) |
| 500.10 | Qualified cybersecurity staff | The bank's; the host's staff are trained (H-AT-2-01) |
| 500.11 | A third-party service provider policy | Above; the bank's policy and contract (B-SA-9-02), and its notice to the FDIC (B-SA-9-03) |
| 500.12 | Multi-factor authentication for every person who signs in (from 1 November 2025) | Supported: security keys and passkeys, the bank's or each customer's requirement of one, SSO with the identity provider's second factor; the bank requires it of everyone (B-IA-2(2)-01) |
| 500.13 | An asset inventory; disposal of data no longer needed | The bank lists OpenClear (B-CM-8-01); retention settings and the data export; the host certifies deletion when a bank leaves (H-MP-6-01) |
| 500.14 | Monitoring users' activity; controls against malicious code; training | Security events to the bank's SIEM (OC-AU-04); uploads scanned for malware; training is the bank's and the host's |
| 500.15 | Encryption policy | Above (500.11) |
| 500.16 | Incident response and continuity plans, tested yearly | The bank's (B-IR-6-01, B-CP-2-01); the host's plan and restores (H-IR-8-01, H-CP-9-01); the runbook's Incidents section |
| 500.17 | 72-hour notice to DFS; the April 15 certification | The bank's (B-IR-6-07, B-CA-2-01); OpenClear's notice tells it when to start, and its evidence export supports the certification |

## Dates

The Second Amendment took effect on 1 November 2023; its last phase (multi-factor authentication
for everyone, and the asset inventory) on 1 November 2025.

## Sources

Read 22 October 2026: 23 NYCRR Part 500 as amended in November 2023 (DFS's compiled text, which it
marks as not the official version; the official text is in the NYCRR); DFS industry letter of
21 October 2025, *Guidance on Managing Risks Related to Third-Party Service Providers*. The phase
dates are worked out from 500.22. Whether 500.12 reaches a bank's business customers signing in to
OpenClear is unconfirmed; the bank decides (B-IA-8-01).
