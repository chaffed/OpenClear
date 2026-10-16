# Compliance

These pages are for a bank's compliance, risk and audit staff, and for examiners. They map
what OpenClear does onto the guidance a bank is examined against, and say where the evidence
for each point is.

- [FFIEC IT Examination Handbook](ffiec.md)
- [Nacha Operating Rules](nacha.md)
- [Model risk management (SR 11-7 / OCC 2011-12)](sr-11-7.md)
- [Third-party risk management (2023 interagency guidance)](tprm.md), with a due-diligence pack
- [OpenClear's controls](controls.md): what OpenClear itself does, each control with its NIST
  SP 800-53 controls, its checks and the CUECs it relies on
- [Complementary user entity controls](cuecs.md): what OpenClear relies on the bank, its
  customers and its host to do, each filed under its NIST SP 800-53 control, to map onto your
  own control inventory. In OpenClear (Settings → CUECs) each is mapped to your control and
  attested each period: automated ones proposed from OpenClear's test history, and every
  accepted attestation signed

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
- **Keys:** generating, storing and rotating the signing keys and the database encryption
  key. See the README, "Signing keys", and `OPENCLEAR_SSO_ENCRYPTION_KEY_PREVIOUS` for
  rotating the encryption key.
- **Configuration:**
  - segregation of duties (every bank starts in *warn* mode; turn on *enforce* once no
    group combines conflicting duties);
  - dual control on decisions;
  - second factors for admins;
  - the daily anomaly-capture schedule (`OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER`).
- **Process:** access reviews, incident response, the annual Nacha Rules compliance audit,
  and the bank's own model validation.
