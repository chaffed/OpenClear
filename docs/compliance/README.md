# Compliance

These pages are for a bank's compliance, risk and audit staff, and for examiners. They map
what OpenClear does onto the guidance a bank is examined against, and say where the evidence
for each point is.

- [Check law: UCC Articles 3 and 4, Regulation CC, Check 21 and ECCHO](check-law.md) (a draft, for counsel's review)
- [FFIEC IT Examination Handbook](ffiec.md), including *Retail Payment Systems* and *Business Continuity Management*
- [GLBA: the Interagency Guidelines Establishing Information Security Standards](glba.md) (12 CFR 364, Appendix B)
- [The CRI Profile](cri-profile.md): answering it from the [NIST CSF 2.0 crosswalk](csf.md)
- [New York DFS Part 500](nydfs.md): OpenClear's target for state rules, as a bank's service provider
- [Incident notification](incident-notification.md): the FDIC's rule, Part 500's, and how the host tells a bank
- [State rules](state-rules.md): Massachusetts 201 CMR 17.00 and the CSBS tools (R-SAT)
- [Check fraud, SARs and OFAC](fincen-ofac.md): what a SAR narrative needs from OpenClear, and why it doesn't screen
- [Nacha Operating Rules](nacha.md)
- [Model risk management (SR 11-7 / OCC 2011-12)](sr-11-7.md)
- [AI risk management (NIST AI RMF)](ai-rmf.md): the fraud-scoring model, function by function
- [Accessibility](accessibility.md): WCAG 2.2 AA, Section 508 and the ADA; what's checked and what a VPAT would hold
- [CFPB Section 1033](cfpb-1033.md): why positive pay isn't covered
- [Third-party risk management (2023 interagency guidance)](tprm.md), with a due-diligence pack
- [Security metrics](security-metrics.md): ASVS coverage, advisories fixed, dependency age and
  the SBOM, for the latest release
- [OpenClear's controls](controls.md): what OpenClear itself does, each control with its NIST
  SP 800-53 controls, its checks and the CUECs it relies on
- [Complementary user entity controls](cuecs.md): what OpenClear relies on the bank, its
  customers and its host to do, each filed under its NIST SP 800-53 control, to map onto your
  own control inventory. In OpenClear (Settings → CUECs) each is mapped to your control and
  attested each period: automated ones proposed from OpenClear's test history, and every
  accepted attestation signed
- [OWASP ASVS 5.0](asvs.md): OpenClear against the Application Security Verification Standard, at
  Level 2 and Level 3 for identity and money, each requirement with its status and evidence
- [NIST CSF 2.0](csf.md): both inventories by CSF 2.0 subcategory, following NIST's own mapping
  from CSF 2.0 to SP 800-53. SOC 2 criteria come from AICPA's mapping, which each installation
  loads from its own copy ([Connecting your GRC tool](../guides/grc.md#soc-2))

**These pages are not legal advice.** They describe features and where to find evidence. Each
bank's own risk assessment decides whether its use of OpenClear meets a requirement, and
several requirements depend on how the bank configures and hosts it.

## Where the evidence comes from

| Evidence | Where |
|---|---|
| Live control status (PASS / WARN / FAIL) | Settings → Controls; `GET /api/v1/controls/status`; `openclear controls` |
| A signed evidence bundle for a period | Settings → Controls → Export evidence; `GET /api/v1/audit/export`; `openclear audit export` |
| Every state-changing action, who did it and when | Settings → Action log (hash-chained, each entry ECDSA-signed) |
| Model validation (back-testing and ongoing monitoring) | Settings → Model risk: file replay and the anomaly-capture test |
| Segregation of duties and two-person changes | Settings → Segregation of duties; Settings → Changes |
| Who has access, and whether they use it | Users → Export (CSV or JSON), with last sign-in |

The evidence bundle is canonical JSON signed with the installation's file-signing key
(ECDSA P-256, SHA-256), and carries the public key needed to check it. A PDF of the same
bundle shows its SHA-256 and signature on the last page. The bundle contains:
- the software version and database revision;
- the matching-rule code and plugins in force, with a SHA-256 of each;
- the scoring models in force, with a SHA-256 of each model file;
- security groups and the duties each one combines;
- whether the action log verifies;
- segregation-of-duties breaches, and changes with their approvals;
- every decision, with who recommended it and who made it;
- model-risk test runs;
- the control status at the time of export.

## What the bank is responsible for

The full list, with a NIST SP 800-53 control for each, is the [CUEC inventory](cuecs.md).
In short: OpenClear can only report what it can see. These stay with the bank, or with
whoever hosts OpenClear for it:

- **Hosting:** disk and database encryption, backups, network security, patching the
  operating system and database.
  - Record how disk and database encryption are done in `OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION`.
    The Controls page then shows that statement; without it, the control warns.
  - Record the recovery time and point objectives in `OPENCLEAR_RECOVERY_OBJECTIVES_ATTESTATION`
    and the last restore test's date in `OPENCLEAR_LAST_RESTORE_TEST` (YYYY-MM-DD); the control
    warns without them, or once the test is over a year old.
- **Keys:** generating, storing and rotating the signing keys and the database encryption
  key. See the README, "Signing keys", and `OPENCLEAR_SSO_ENCRYPTION_KEY_PREVIOUS` for
  rotating the encryption key.
- **Configuration:**
  - segregation of duties (every bank starts in *warn* mode; turn on *enforce* once no
    group combines conflicting duties);
  - dual control on decisions;
  - second factors for admins;
  - scheduled control testing (`OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER`): the hourly and
    nightly checks, the daily anomaly capture, and each period's proposed attestations. Without
    it there's no test history, and automated CUECs are attested "not tested".
- **Process:** access reviews, incident response, the annual Nacha Rules compliance audit,
  and the bank's own model validation.
