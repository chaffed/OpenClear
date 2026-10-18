# Cryptography and keys

Every key and secret OpenClear uses, what protects it, and how it's rotated. It answers ASVS 5.0
11.1.1–11.1.4. The libraries are `cryptography`, `bcrypt`, PyJWT, joserfc, python3-saml (xmlsec)
and py_webauthn; OpenClear implements no cryptographic primitive itself.

## Keys and secrets

| Key or secret | Used for | Algorithm | Kept | Rotated by |
|---|---|---|---|---|
| Session signing key pair | Signing session and API tokens (`iss` and `aud` checked) | ECDSA P-256 (ES256) | Files named in `jwt_*_key_path` | Service provider, on the bank's schedule (`key_age` control) |
| File signing key pair | Signing uploaded files' digests and the evidence export | ECDSA P-256, SHA-256 | Files (`file_signing_*`) | Service provider |
| Audit log signing key pair | Signing each action-log entry in its chain | ECDSA P-256, SHA-256 | Files (`audit_log_signing_*`) | Service provider |
| Unauthorized-debit statement key pair | Signing Written Statements of Unauthorized Debit | ECDSA P-256 | Files (`wsud_signing_*`) | Service provider |
| Field encryption key | Encrypting secrets in the database: SSO client secrets, SAML private keys, webhook secrets, plugin settings marked secret | AES-256-GCM, key from HKDF-SHA256 of `sso_encryption_key` | Environment / secret store | Service provider: set the old value in `sso_encryption_key_previous`; values re-encrypt at start |
| SAML signing keys | Signing requests to a SAML identity provider | RSA-3072 for connections made since 1.22.1 (RSA-2048 before; replacement in issue #21) | Database, encrypted with the field key | Recreate the connection |
| Passwords | Signing in | bcrypt over HMAC-SHA-256 (`ocv2:`) | Database (hash only) | The person |
| API client secrets, SCIM tokens, embedded launch codes, invitation and reset links, platform API keys | Authenticating systems and one-time links | SHA-256 of a random value of at least 256 bits | Database (hash only) | Rotated or reissued in the app |
| API client key pairs | `private_key_jwt` client authentication | RSA 2048+ (RS/PS 256–512) or ECDSA (ES256, ES384), as the client registers | Database (public key only) | The client's owner |
| Webhook signing secrets | Signing outgoing webhooks | HMAC-SHA-256 | Database, encrypted | Rotated per webhook |
| Security keys and passkeys | Second factor | WebAuthn (ES256 / RS256 per authenticator) | Database (public key only) | The person; an administrator can reset |

Production refuses to start with the checked-in development keys (`config.assert_production_safe`).
Random values come from the operating system's generator (`secrets`). Secrets are compared in
constant time.

## In transit

TLS ends at the bank's edge (a CUEC); OpenClear sets HSTS and refuses outbound connections to
non-public addresses. Outbound calls verify certificates; SIEM delivery needs TLS 1.2 or newer
and, in production, a verified certificate.

## Moving to new algorithms

- **Versioned formats.** Encrypted values carry their scheme and key id (`v2:<key id>:`),
  passwords their scheme (`ocv2:`), and tokens their algorithm, so a new algorithm can be added
  beside the old one and values upgraded as they're used, as the move from Fernet to AES-GCM
  (1.9) and to pre-hashed passwords (1.22.1) were.
- **Post-quantum.** The signing keys (ECDSA P-256) and SAML keys (RSA) are the parts quantum
  computers would threaten. When the libraries above support NIST's ML-DSA, the session and
  file signing keys can move to it with the same versioning; the bank's identity providers and
  WebAuthn authenticators move at their own pace. AES-256 and SHA-256 are expected to remain
  adequate.
- **Finding uses.** Bandit runs on every change and fails the build on a weak hash used for
  security; CodeQL reports weak ciphers and protocols. This page is the inventory: a new key or
  algorithm is added here in the same change.
