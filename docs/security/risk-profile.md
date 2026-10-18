# Risk profile and data classification

What OpenClear holds, how sensitive each kind of data is, and what protection each level
requires. It sets the security requirements OpenClear is held to: ASVS 5.0 Level 2, and
Level 3 where identity and money are at stake ([ADR 0001](../adr/0001-asvs-level-2-with-level-3-for-identity-and-money.md)).
The [threat model](threat-model.md) shows how the data moves. Terms are as in
[CONTEXT.md](../../CONTEXT.md).

## Why OpenClear is high-risk

- **It decides payments.** A bank's customers use OpenClear to pay or return checks and ACH
  debits that don't match what they expected. A wrong decision either pays a fraudulent item or
  returns a legitimate one, often irreversibly once the return deadline passes.
- **It holds what fraudsters want.** Account numbers, check images (with signatures and payee
  names), and the patterns of what a business pays.
- **It's shared.** One installation serves several banks, and each bank serves many customers.
  A failure of isolation exposes other institutions' data.
- **It's examined.** Banks are examined against the FFIEC handbook, Nacha rules and model-risk
  guidance. OpenClear's records are part of their evidence.

## Who might attack it

| Actor | Wants | Main protections |
|---|---|---|
| Outside fraudster | Get a fraudulent item paid; learn account numbers | Sign-in protections, the exception workflow, signed files |
| Customer insider | Approve their own payment; see another customer's data | Segregation of duties, dual control, customer and account scope |
| Bank insider | Change decisions or settings unnoticed; reach another bank | Two people for settings changes, the signed action log, row-level security |
| Another bank's admin or identity provider | Reach a person's other bank | Sign-in limited to its own scope; switching checks the target's rules |
| Service provider's staff | Read a bank's data | Support access only by the bank's grant; two people for provider actions |
| Supply-chain attacker | Run code through a dependency, a plugin or a model file | Pinned dependencies and scanning (OWASP_PLAN.md OW2), signed model files, plugins chosen by the service provider |

## Data classification

| Level | What | Examples |
|---|---|---|
| **Secret** | Lets someone act as a person, a system or OpenClear | Passwords (hashed), session tokens, security-key assertions, API client secrets and keys, signing and encryption keys, SSO client secrets, webhook secrets |
| **Restricted** | Payment data and what identifies a bank's customers' money | Account numbers, issued items, check images, ACH entries and authorizations, payment files, exceptions and decisions |
| **Sensitive** | Who did what, and how OpenClear is configured | The action log, security events, people and their memberships, notifications, settings, control results |
| **Internal** | Operational data with no customer content | Job status, rate-limit counters, health |
| **Public** | Published by the project | Documentation, the API schema, SDKs |

## Protection by level

| Requirement | Secret | Restricted | Sensitive | Internal |
|---|---|---|---|---|
| **In transit** | TLS; never in URLs or logs | TLS; not in URLs ([#14](https://github.com/chaffed/OpenClear/issues/14)) | TLS | TLS |
| **At rest** | Hashed (passwords, API secrets) or encrypted with AES-256-GCM (OC-SC-01); keys outside the database | The service provider's disk and database encryption (OC-SC-02) | As Restricted | Service provider's choice |
| **Integrity** | — | Files kept with SHA-256 and a signature (OC-SC-03) | Action log chained and signed (OC-AU-01) | — |
| **Who can read it** | No one through OpenClear's pages: a saved secret isn't shown again (plugin settings, for example) | Permitted people of that bank, and of that customer and its accounts (OC-AC-01, -03, -04) | Admins of that bank or customer; the bank's SIEM | Service provider |
| **Logging** | Never logged, never in a security event | Not in security events; changes in the action log | Is the log | — |
| **Outside services** | Never sent | Only to services the bank turns on and reviews (cloud OCR, webhooks to its own systems) | Security events to the bank's own SIEM | — |
| **Browser** | HttpOnly cookies only | `Cache-Control: no-store`; cleared when the session ends ([#13](https://github.com/chaffed/OpenClear/issues/13)) | `no-store` | — |
| **Retention** | Until rotated or revoked | The bank's own records policy; uploads and exports by installation settings | Action log archived to signed files (OC-AU-03); security events 30 days by default | Installation settings |

Where a requirement depends on the service provider or the bank (disk encryption, key
storage, TLS at the edge), it's a CUEC ([CUECs](../compliance/cuecs.md)).

## Where this is checked

- **[ASVS 5.0 scope](asvs-5.0-scope.csv):** which requirements apply, by level.
- **[Threat model](threat-model.md):** the threats against each boundary and what mitigates them.
- **[OpenClear's controls](../compliance/controls.md):** each protection above, with its live check.
