# OpenClear's controls

What OpenClear itself does to meet each control objective, filed under the
[NIST SP 800-53 Rev. 5.1.1](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) controls it carries out. Several
rely on the bank, its customers or whoever runs OpenClear to do their part: those are the
[complementary user entity controls](cuecs.md) (CUECs). It isn't legal advice
([Compliance](README.md)).

Inventory version 1.0. The same list, with each check's live status for your bank, is at
`GET /api/v1/compliance/controls`, and on the Controls page.

## Reading the inventory

- **Id**: `OC-<family>-<nn>`. Ids never change, and a withdrawn one is never reused.
- **NIST**: the 800-53 Rev. 5 controls it carries out, the main one first.
- **Type**: *preventive* stops something happening; *detective* shows that it happened; *corrective* puts it right.
- **Nature**: *built in*, always on; *configurable*, depends on a bank's setting. A configurable control relies on the CUEC that sets it.
- **Checks**: the tests of the control. *Live* checks are on the Controls page today;
  *planned* ones arrive with CONTROLS_PLAN.md CT2 and CT3.

## Access control

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-AC-01 | **Permissions checked on every request.** Every page, API call and background job checks the person's or API client's permissions, from their security group, before it reads or changes anything. | **AC-3** Access Enforcement<br>**AC-6** Least Privilege | preventive, built in | `privileged_accounts` (planned) | B-AC-6-01 | Settings → Security groups; auth/permissions.py |
| OC-AC-02 | **Access ends when a person leaves.** Adding, changing and removing a person's access is recorded in the action log, and a deactivated person can't sign in or use a token from that moment. An identity provider can do the same through SCIM. | **AC-2** Account Management<br>**AC-2(3)** Disable Accounts<br>**AC-2(1)** Automated System Account Management | preventive, built in | `dormant_accounts` (planned)<br>`access_review` (planned)<br>`scim_sync` (planned) | B-AC-2-01, B-AC-6(7)-01, B-AC-2(1)-01 | Users; the action log |
| OC-AC-03 | **Customers see only their own.** A business customer's people reach only their own customer's accounts, items and decisions, and never the bank's own permissions. A customer manages its own people and sign-on only when the bank allows it. | **AC-3** Access Enforcement<br>**AC-4** Information Flow Enforcement<br>**AC-6** Least Privilege | preventive, configurable | `customer_self_service` (live)<br>`customer_dormant_users` (planned)<br>`customer_access_review` (planned) | B-AC-2-02, C-AC-2-01, C-AC-6(7)-01 | A customer's page → Self-service; People |
| OC-AC-04 | **Access by account.** A customer's person can be limited to some of the customer's accounts; every list, item, decision, image, upload and export they reach is limited to those accounts. | **AC-3** Access Enforcement<br>**AC-6** Least Privilege | preventive, configurable | — | C-AC-2-01 | People → Choose accounts; db/account_scope.py |
| OC-AC-05 | **Segregation of duties.** Conflicting permissions can't be combined in one security group, and whoever submitted an item can't decide its exception. Each bank chooses to warn or enforce; every breach attempt is logged. | **AC-5** Separation of Duties | preventive, configurable | `segregation_of_duties` (live)<br>`customer_sod` (planned) | B-AC-5-01, C-AC-5-01 | Settings → Segregation of duties |
| OC-AC-06 | **Dual control on decisions.** When a bank turns dual control on, one person recommends a decision and a different person approves it. | **AC-3(2)** Dual Authorization<br>**AC-5** Separation of Duties | preventive, configurable | `dual_control` (planned) | B-AC-3(2)-01 | Settings → Dual control; Approvals |
| OC-AC-07 | **Two people for rule and model changes.** Turning on a matching rule and promoting a fraud model wait for a second admin's approval under enforced segregation of duties, and both steps are logged. | **CM-3** Configuration Change Control<br>**CM-5** Access Restrictions for Change<br>**AC-3(2)** Dual Authorization | preventive, configurable | `two_person_changes` (planned) | B-CM-3-01 | Settings → Changes |
| OC-AC-08 | **Support access only by grant.** Platform staff reach a bank's data only through a grant the bank makes: named, with a reason and an end, read-only unless a second admin allows more, and never to decide. | **AC-3** Access Enforcement<br>**MA-4** Nonlocal Maintenance<br>**AC-6(5)** Privileged Accounts | preventive, built in | `support_access_review` (planned)<br>`platform_staff` (planned) | B-MA-4-01, H-AC-6(5)-01 | Settings → Support access |
| OC-AC-09 | **Banks kept apart.** Every query runs inside one bank's context, and on PostgreSQL row-level security refuses another bank's rows even if a query forgets to filter. | **AC-4** Information Flow Enforcement<br>**AC-3** Access Enforcement | preventive, built in | — | — | db/tenancy.py; the RLS policies in migrations/ |
| OC-AC-10 | **Sessions end.** Sessions always end after an idle and an absolute timeout, whose lengths the bank sets, and a person can sign out their other devices. | **AC-12** Session Termination<br>**SC-23** Session Authenticity | preventive, built in | — | — | Settings → Session timeouts |
| OC-AC-11 | **Model data pooling disclosed.** A bank on the shared fraud model acknowledges the data-pooling disclosure, recorded with who and when, and can move to a model trained on its own decisions alone. | **AC-21** Information Sharing | preventive, configurable | `model_pooling_acknowledged` (planned) | B-AC-21-01 | Settings → Fraud model |
| OC-AC-12 | **ACH debits only from authorized originators.** An ACH debit from an originator not on the account's authorizations becomes an exception for the customer to decide. | **AC-3** Access Enforcement | preventive, configurable | `stale_ach_authorizations` (planned) | C-AC-3-01 | ACH → Authorizations |

## Identification and authentication

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-IA-01 | **Password policy and lockout.** Passwords meet the bank's policy (length and the kinds of character required), and repeated failed sign-ins lock the account for a time. | **IA-5(1)** Password-based Authentication<br>**AC-7** Unsuccessful Logon Attempts | preventive, configurable | `password_policy` (planned) | B-IA-5(1)-01 | Settings → Password policy |
| OC-IA-02 | **Security keys and passkeys.** People sign in with a FIDO2 security key or passkey as a second factor, which a bank or customer can require. | **IA-2(1)** Multi-factor Authentication to Privileged Accounts<br>**IA-2(2)** Multi-factor Authentication to Non-privileged Accounts<br>**IA-2(8)** Access to Accounts — Replay Resistant | preventive, configurable | `admin_mfa` (live)<br>`staff_mfa` (planned)<br>`customer_mfa` (planned) | B-IA-2(1)-01, B-IA-2(2)-01, C-IA-2-01, B-IA-8-01 | My security; Settings → Password policy |
| OC-IA-03 | **Single sign-on and SCIM.** A bank, or a customer the bank allows, signs its people in through its own identity provider (OIDC or SAML), and can provision and remove them through SCIM. | **IA-2** Identification and Authentication (Organizational Users)<br>**IA-8** Identification and Authentication (Non-organizational Users)<br>**AC-2(1)** Automated System Account Management | preventive, configurable | `scim_sync` (planned)<br>`customer_sso` (planned) | B-AC-2(1)-01, C-AC-2(1)-01 | Settings → Sign-on; a customer's Sign-on |
| OC-IA-04 | **Integration credentials.** API clients authenticate with a secret kept only as a hash, or with a key pair (private_key_jwt, each assertion usable once), and can be limited to reading or to one customer. | **IA-9** Service Identification and Authentication<br>**IA-5** Authenticator Management | preventive, built in | `api_client_secrets` (live)<br>`customer_api_client_secrets` (planned) | B-IA-5-01, C-IA-5-01 | Settings → API clients |
| OC-IA-05 | **Customers' sign-in set by the bank.** The bank decides how each customer's people sign in: password and second factor, or the customer's own identity provider. | **IA-8** Identification and Authentication (Non-organizational Users) | preventive, configurable | `customer_mfa` (planned) | B-IA-8-01 | A customer's page → Sign-on |

## Audit and accountability

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-AU-01 | **Signed, chained action log.** Every change is recorded with who, when, from where and what, each entry chained to the last and signed (ECDSA), so an entry can't be altered or removed unnoticed. | **AU-2** Event Logging<br>**AU-3** Content of Audit Records<br>**AU-9(3)** Cryptographic Protection<br>**AU-10** Non-repudiation<br>**AU-12** Audit Record Generation | detective, built in | `audit_chain` (live) | — | Action log |
| OC-AU-02 | **The chain verified.** The action log's chain and signatures are verified daily and on request, and the Controls page shows the last result. | **AU-9** Protection of Audit Information<br>**SI-7** Software, Firmware, and Information Integrity | detective, built in | `audit_chain` (live) | — | Controls → Action log integrity |
| OC-AU-03 | **Signed archives.** Old action-log entries are archived to signed files before they leave the database, so they still verify. | **AU-11** Audit Record Retention<br>**AU-9(3)** Cryptographic Protection | preventive, built in | — | H-AU-9(2)-01 | services/audit_archive_service.py |
| OC-AU-04 | **Security events delivered.** Sign-ins, failures, lockouts, permission changes and control status changes become security events, delivered with retries to the bank's and the installation's SIEM. | **AU-12** Audit Record Generation<br>**AU-6(1)** Automated Process Integration<br>**SI-4** System Monitoring | detective, configurable | `event_delivery` (live) | B-AU-6-01, H-AU-9(2)-01 | Settings → Security event forwarding |
| OC-AU-05 | **Signed evidence export.** For any period, a signed bundle of the version, rules and models in force, the chain's verification, breaches, changes, decisions and control status, which an examiner can check with the public key. | **AU-7** Audit Record Reduction and Report Generation<br>**AU-11** Audit Record Retention | detective, built in | `evidence_export` (planned) | B-AU-11-01 | Controls → Export evidence |

## System and communications protection

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-SC-01 | **Secrets encrypted in the database.** Secrets kept in the database (SSO keys, delivery credentials, plugin settings) are encrypted with AES-256-GCM under versioned keys, and re-encrypted when a key is rotated. | **SC-28(1)** Cryptographic Protection<br>**SC-12** Cryptographic Key Establishment and Management<br>**SC-13** Cryptographic Protection | preventive, built in | `field_encryption` (live)<br>`key_age` (planned) | H-SC-12-01 | auth/crypto.py |
| OC-SC-02 | **Encryption at rest stated.** The host's statement of how disks and the database are encrypted is shown on the Controls page, which warns until there is one. | **SC-28** Protection of Information at Rest | detective, built in | `storage_encryption` (live) | H-SC-28(1)-01 | Controls → Storage encryption |
| OC-SC-03 | **Files signed.** Every uploaded file is kept with its SHA-256 and a signature, and every webhook and evidence bundle OpenClear sends is signed. | **SI-7(6)** Cryptographic Protection<br>**SC-8** Transmission Confidentiality and Integrity | preventive, built in | — | — | An upload's page; webhooks |
| OC-SC-04 | **Browser protections.** Cookies are HTTP-only, and secure over HTTPS, which is marked with HSTS; every form carries a CSRF token; and a content security policy limits scripts to OpenClear's own. | **SC-23** Session Authenticity<br>**SC-8** Transmission Confidentiality and Integrity<br>**SC-18** Mobile Code | preventive, built in | `transport_security` (planned) | H-SC-8(1)-01 | web/security.py |
| OC-SC-05 | **Edge trusted only as configured.** The caller's address is taken only from the configured edge's header, an edge secret can be required, and the Controls page shows the edge in front. | **SC-7** Boundary Protection | preventive, configurable | `edge` (live) | H-SC-7-01 | Controls → Edge in front; docs/guides/edge.md |
| OC-SC-06 | **Rate limits.** Sign-ins, the API and expensive pages are rate limited per person, API client and address. | **SC-5** Denial-of-service Protection<br>**AC-7** Unsuccessful Logon Attempts | preventive, built in | — | — | web/rate_limit.py |
| OC-SC-07 | **Outbound connections checked.** Webhooks and event destinations must be public addresses, checked when connecting, and are reported when not HTTPS. | **SC-7** Boundary Protection<br>**SC-8** Transmission Confidentiality and Integrity | preventive, built in | `integration_tls` (planned) | B-SC-8-01 | Settings → Webhooks |

## System and information integrity

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-SI-01 | **Malformed files refused.** X9.37, NACHA and delimited files go through hardened parsers that refuse malformed input quickly and cleanly; a corpus of bad files is run through them on every check. | **SI-10** Information Input Validation | preventive, built in | `parser_hardening` (live) | — | Controls → Malformed-file handling |
| OC-SI-02 | **Uploads checked row by row.** Every upload row is validated and its result kept, failed rows can be corrected in the browser, and an upload can be backed out. | **SI-10** Information Input Validation<br>**SI-7** Software, Firmware, and Information Integrity | corrective, built in | `rejected_rows` (planned)<br>`file_reconciliation` (planned) | C-SI-10-01, B-SI-7-01 | An upload's page |
| OC-SI-03 | **Anomaly capture tested.** A daily test injects known fraud patterns (duplicate serials, altered amounts, wrong accounts, malformed fields) into the bank's live rules in a dry run and records whether each was caught. | **SI-4** System Monitoring<br>**CA-2** Control Assessments | detective, configurable | `anomaly_capture` (live) | B-CA-7-02 | Model risk |
| OC-SI-04 | **Replay before a change.** A historical file can be replayed through today's rules in a dry run, showing how the result would differ, before a rule or model change is approved. | **CM-4** Impact Analyses<br>**CA-2** Control Assessments | detective, built in | `change_impact_replay` (planned) | B-CM-4-01 | Model risk → Replay |
| OC-SI-05 | **Decisions before the deadline.** Each exception shows its deadline, reminders go out before the cutoff, and an undecided exception takes the account's default disposition. | **SI-4** System Monitoring<br>**CP-2** Contingency Plan | corrective, configurable | `sla` (live)<br>`default_dispositions` (planned)<br>`customer_decision_timeliness` (planned) | B-SI-4-01, B-CP-2-01, C-SI-4-01 | Exceptions; Settings → Reminders |
| OC-SI-06 | **Decisions delivered to the core.** Decisions go to the core through an outbox with retries, as files or calls, so none is lost if the core is down. | **CA-3** Information Exchange | corrective, built in | `decision_delivery` (planned) | B-CA-3-01 | Settings → Decision files |
| OC-SI-07 | **Unauthorized debits signed for.** A customer signs a Written Statement of Unauthorized Debit for a returned consumer ACH debit, kept with its consent and attestation text. | **IR-6** Incident Reporting<br>**AU-10** Non-repudiation | detective, built in | `wsud_signed` (planned) | C-IR-6-01 | Written statements of unauthorized debit |

## Configuration management

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-CM-01 | **Production settings enforced.** In production OpenClear refuses to start with the checked-in keys and secrets, placeholder legal text or incomplete storage settings, and the Controls page reports the settings in force. | **CM-6** Configuration Settings | preventive, built in | `production_safety` (live) | H-CM-6-01 | Controls → Production safety |
| OC-CM-02 | **Versioned releases and migrations.** Each release is versioned with a changelog, its database migrations upgrade and downgrade, and the running version is published. | **CM-3** Configuration Change Control<br>**SI-2** Flaw Remediation<br>**CP-10** System Recovery and Reconstitution | preventive, built in | `software_currency` (planned) | H-SI-2-01 | /openapi.json; CHANGELOG.md |
| OC-CM-03 | **What's running is recorded.** The evidence export records the version, the plugins and models in force with their digests, and the outside services turned on. | **CM-2** Baseline Configuration<br>**CM-8** System Component Inventory | detective, built in | `outside_services` (planned) | B-SA-9-01 | Controls → Export evidence |

## Assessment and monitoring

| Id | Control | NIST | Type | Checks | Relies on | Where |
|---|---|---|---|---|---|---|
| OC-CA-01 | **Controls checked continuously.** Each control is checked when the Controls page or API is read and hourly; a change of status becomes a security event. | **CA-7** Continuous Monitoring | detective, built in | `control_response` (planned) | B-CA-7-01 | Controls |
| OC-CA-02 | **Health and jobs reported.** /health reports whether the database answers, and each scheduled job's last run is kept. | **CA-7** Continuous Monitoring<br>**SI-4** System Monitoring | detective, built in | `scheduled_jobs` (planned) | H-SI-4-01 | /health |

