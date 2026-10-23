# GLBA: Interagency Guidelines Establishing Information Security Standards

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

For an FDIC-supervised bank the Gramm-Leach-Bliley Act's safeguards are 12 CFR Part 364,
Appendix B, with Supplement A on responding to unauthorized access to customer information.
They bind the bank. Whoever hosts OpenClear for the bank is its *service provider* (I.C.2), and
the Guidelines reach it only through the bank's contract (III.D). This page maps each element to
OpenClear's [controls](controls.md) and the [CUECs](cuecs.md). It isn't legal advice
([Compliance](README.md)).

**Whose information.** The Guidelines protect information about *consumers* who are the bank's
customers. Positive pay accounts are mostly businesses', but OpenClear also holds consumer ACH
debits, sole proprietors' accounts and payees' names, and an account number with a name is
"sensitive customer information" under Supplement A. Treat everything in OpenClear as customer
information. (How examiners treat business accounts' data here is a question for counsel.)

| Element | What the bank must do | OpenClear | Bank's and host's part |
|---|---|---|---|
| Program and board (II, III.A, III.F) | A written program approved by the board; a yearly report to it, covering service providers | — | B-RA-3-01 |
| Risk assessment (III.B) | Foreseeable threats, their likelihood and damage, and whether controls suffice | The controls, [threat model](../security/threat-model.md) and evidence export to assess against | B-RA-3-01 |
| Access controls (III.C.1.a) | Only authorized people; guard against pretexting | OC-AC-01 to OC-AC-05, OC-AC-08 to OC-AC-10, OC-IA-01 to OC-IA-05 | B-AC-2-01, B-AC-6(7)-01 |
| Physical access (III.C.1.b) | Restrict it where customer information is | — | The host's, assessed under B-SA-9-01 |
| Encryption (III.C.1.c) | In transit and at rest where others could reach it | OC-SC-04 (HTTPS), OC-SC-01 (secrets); disks and the database by the host, stated under OC-SC-02 | H-SC-28(1)-01, C-SC-28-01 |
| Change (III.C.1.d) | Changes consistent with the program | OC-AC-07, OC-AC-13, OC-CM-02, OC-CM-03 | B-CM-3-01 |
| Dual control, segregation, screening (III.C.1.e) | For staff with access to customer information | OC-AC-05, OC-AC-06 | B-AC-5-01; H-PS-3-01 for the host's staff |
| Monitoring (III.C.1.f) | Detect attacks and intrusions | OC-AU-04, OC-CA-01 | B-SI-4-01, H-SI-4-01 |
| Response program (III.C.1.g; Supplement A) | Assess the scope; tell the FDIC as soon as possible; file a SAR when required; contain; tell customers when misuse is reasonably possible | The signed action log, including who viewed check images and who exported data, shows whose information was reached, so notice can be limited to them; the host's notices (OC-IR-01) | B-IR-6-01; H-IR-6-01, H-IR-6-02 |
| Environmental and technical failure (III.C.1.h) | Protect against loss | The host's recovery objectives and restore test (OC-CP-01) | H-CP-2-01, H-CP-9-01 |
| Training and testing (III.C.2, III.C.3) | Train staff; test key controls, independently | Live controls (OC-CA-01), anomaly tests (OC-SI-03) | B-AT-3-01, B-CA-7-02 |
| Disposal (II.B.4, III.C.4) | Dispose of customer information properly | **A gap.** OpenClear keeps a bank's business records until the bank's data is deleted, and exports all of it; it has no bank-set retention for items, issued checks and images, and no signed record of deletion at exit | B-SI-12-01 |
| Service providers (III.D) | Due diligence; a contract to meet the Guidelines; monitoring, reviewing audits or test results | [Third-party risk](tprm.md); the signed evidence export; the host's attestations on the Controls page | B-SA-9-01, B-SA-9-02 |

## Unauthorized access at the host

Supplement A expects the contract to require the provider to tell the bank as soon as possible of
any unauthorized access to its customer information. That's wider than the FDIC's computer-security
incident rule ([Incident notification](incident-notification.md)), which is about disruption, so
the host has its own CUEC for it (H-IR-6-02) and uses the same notices. The bank, not the
provider, tells its customers and the FDIC, unless it contracts the provider to do it.

## Sources

Read 22 October 2026: 12 CFR Part 364, Appendix B and Supplement A (eCFR, current to 21 October
2026; 80 FR 65907, as amended by 91 FR 18294, effective 9 June 2026). The 2026 amendment only
removed a reference to reputation risk from Supplement A.
