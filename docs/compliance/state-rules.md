# State rules

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

OpenClear's banks are state-chartered, so their state banking department examines them alongside
the FDIC. OpenClear takes **New York DFS Part 500** as its benchmark for state rules, the most
detailed of them ([its own page](nydfs.md)). This page covers the other two most often met:
Massachusetts' data security regulation, which reaches any bank with Massachusetts customers, and
the tools the Conference of State Bank Supervisors (CSBS) gives state examiners. It isn't legal
advice ([Compliance](README.md)).

## Massachusetts 201 CMR 17.00

Massachusetts' regulation applies to anyone who owns or licenses *personal information* about a
Massachusetts resident: a name together with a Social Security number, a driver's licence or ID
number, or a financial account number. It applies whatever a bank's charter or home state, and has
no exemption for banks under GLBA; its safeguards are to be consistent with the federal rules that
bind the bank. OpenClear holds account numbers with account names, so for an individual's or a
sole proprietor's account it holds personal information. Whoever hosts OpenClear is a *service
provider* under it.

| Requirement | What OpenClear does | Bank's and host's part |
|---|---|---|
| A written information security program (17.03(1)) | Describes its safeguards in the [third-party pack](tprm.md) and [controls](controls.md), for the bank's program to cite | B-PL-1-01 |
| Oversee service providers, by contract (17.03(2)(f)) | — | B-SA-9-02 |
| Former employees lose access (17.03(2)(e)) | Access ends when a person leaves, including through SCIM (OC-AC-02, OC-IA-03) | B-AC-2-01 |
| Monitor that the program works; document incidents (17.03(2)(h), (j)) | Live controls (OC-CA-01), the signed evidence export (OC-AU-05), the action log and security events (OC-AU-01, OC-AU-04) | B-CA-7-01, B-IR-6-01 |
| Secure sign-in: unique IDs, strong passwords or tokens, lockout, active users only (17.04(1), (2)) | Per-person accounts, password policy and lockout, security keys, SSO, permissions, production settings with no default passwords (OC-IA-01, OC-IA-02, OC-IA-03, OC-AC-01, OC-CM-01) | B-AC-2-01 |
| Encrypt personal information sent over public networks (17.04(3)) | HTTPS only; webhooks signed and sent only to HTTPS addresses (OC-SC-03, OC-SC-04) | H-SC-8(1)-01, B-SC-8-01 |
| Monitor for unauthorized access (17.04(4)) | The signed action log and security events (OC-AU-01, OC-AU-04) | B-AU-6-01 |
| Encrypt personal information on laptops and portable devices (17.04(5)) | Records each export | B-SC-28-01; customers C-SC-28-01 |
| Patches, firewall, anti-malware (17.04(6), (7)) | Scans uploads for malware | H-SI-2-01, H-SC-7-01 |
| Training (17.04(8)) | — | B-AT-3-01, H-AT-2-01 |

The regulation doesn't require encryption at rest on servers; OpenClear expects it anyway
(OC-SC-02, H-SC-28(1)-01).

## CSBS tools

CSBS is the association of state banking regulators. It makes no rules, but publishes tools
that state examiners use alongside the FFIEC booklets. The one most often sent to banks before an
examination is the **Ransomware Self-Assessment Tool (R-SAT)**, version 2.0 (October 2023). It
asks the bank to list its outsourced and cloud services and suggests asking vendors to answer some
questions. For OpenClear:

| R-SAT question | Answer for OpenClear |
|---|---|
| 4. Outsourced or cloud services | List OpenClear under "File imaging (checks)" or "Other critical services", hosted by its host. |
| 6. Vendors' remote access to the bank's network | None. OpenClear never connects into the bank's network; it delivers decisions to HTTPS endpoints the bank names. Platform support staff reach a bank's data only by the bank's grant (OC-AC-08, B-MA-4-01). |
| 12. Backups of image files and outsourced services | The host's backups: encrypted, at least one copy immutable or offline, a clean restore tested yearly (H-CP-9-01); the host's recovery objectives on the Controls page (OC-CP-01). |
| 13. Multi-factor authentication, phishing-resistant preferred | Security keys and passkeys for staff and customers; SSO with the bank's own factors (OC-IA-02, OC-IA-03, OC-IA-05). |
| 16. Alerts on changes to privileged access | Changes to roles and permissions are security events, sent to the bank's SIEM (OC-AU-04). |
| 18. Preserve logs before restoring | The action log is signed and chained, so it can be verified after a restore (OC-AU-02, OC-AU-03). |
| 19. Third parties to call | The host's incident notices and the bank's incident contacts ([Incident notification](incident-notification.md)). |

## Sources

Read 22 October 2026:
- 201 CMR 17.00 as in force since 2010, and OCABR's *Frequently Asked Questions Regarding 201 CMR
  17.00*. mass.gov refuses automated reading, so both were read from the Internet Archive's copy of
  December 2025; mass.gov marks its text unofficial, and the official Code of Massachusetts
  Regulations wasn't checked.
- CSBS, Bankers Electronic Crimes Task Force and US Secret Service, *Ransomware Self-Assessment
  Tool*, version 2.0, 24 October 2023.

**Unconfirmed:** whether a given state's department sends the R-SAT before every examination;
whether Massachusetts reads the definition of "owns or licenses" to bind a host directly, beyond
the bank's contract.
