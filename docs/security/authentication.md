# Authentication and sessions

How people and systems sign in to OpenClear, how strongly each way is protected, and how sessions
work. It answers ASVS 5.0 6.1.1, 6.1.3, 6.3.4, 7.1.1–7.1.3 and 7.6.1. Terms are as in
[CONTEXT.md](../../CONTEXT.md); the defaults below are `config.py`'s, and most can be changed per
bank.

## Ways to sign in

Every way in is listed here; there are no others.

| Who | How | Second factor | Notes |
|---|---|---|---|
| Bank staff and customers' people | Email and password | A security key or passkey, when the bank or customer requires it (after its grace period), or the membership does | `auth/login_service.py`; required keys in `auth/security_key_policy.py` |
| Bank staff and customers' people | Single sign-on: OIDC or SAML, from the bank's or a customer's identity provider | The identity provider's (a [CUEC](../compliance/cuecs.md), B-IA-2(2)-02); a connection can refuse sign-ins the provider doesn't report as multi-factor | Matched by the provider's subject, linked only within the connection's own scope |
| Customers' people inside online banking | An embedded session the online banking site starts for a user it has signed in | Online banking's (CUEC B-IA-2(2)-03) | One-time launch code; can't switch organization |
| The bank's systems (core, middleware) and customers' systems | OAuth 2.0 client credentials: a secret, or a signed assertion (`private_key_jwt`) | Not applicable | A bank can require key pairs |
| Online banking acting for a signed-in person | Token exchange (RFC 8693) of the identity provider's ID token | The identity provider's | Its API client must be linked to the SSO connection |
| The bank's directory | SCIM with a bearer token | Not applicable | Token stored as its hash only |
| Platform staff | Email and password, then always a security key | Always a security key | Their keys are separate from banks' ([ADR 0005](../adr/0005-platform-staff-keys-in-their-own-table.md)) |

Switching organization reuses the current sign-in, so it's allowed only where that sign-in would
have been accepted: a password session only where passwords are allowed, an SSO session only
within its own bank, and never from an embedded session or a customer's own sign-on.

## Passwords

- At least 15 characters for banks created since 1.22 (8 for older banks unless they raised it),
  with no required character classes unless the bank or customer adds them.
- Refused if among the 100,000 most common breached passwords (bundled), in a larger list the
  service provider loads, or reported by Have I Been Pwned's range API when turned on; or if it
  contains OpenClear, the bank's or customer's name, or the person's email.
- Checked again at each sign-in: a password now too short, or breached, must be changed before
  going further.
- Stored as bcrypt of an HMAC-SHA-256 of the password, so every character counts.
- No password expiry and no secret questions. A forgotten password is reset by an administrator,
  which emails the person a one-time link; the administrator never sees a password.

## Defences against guessing

| Defence | Default | Where |
|---|---|---|
| Lockout after failed passwords | 5 failures, 15 minutes (per bank; can last until an administrator unlocks) | `login_max_failed_attempts`, `login_lockout_minutes` |
| Requests per IP address | 120 a minute | `rate_limit_per_minute` |
| Requests per signed-in person or API client | 600 a minute | `rate_limit_per_principal_per_minute` |
| Token endpoint, invitation and reset links | 30 a minute | `oauth_token_rate_limit_per_minute` |
| The same answer for a wrong password, an unknown email and a locked account | Always | Including the time taken |

## Sessions

| Setting | Default | Why |
|---|---|---|
| Idle timeout (the access token's lifetime) | 30 minutes | Short enough that an unattended screen doesn't stay signed in; activity renews it |
| Absolute lifetime (the refresh token's) | 7 days | A working week of renewed sessions; nobody stays signed in past it whatever their activity |
| Security-key step | 5 minutes | Only long enough to touch a key |
| Recent sign-in for changing security keys | 10 minutes | A stolen session can't swap in its own key |
| API client tokens | 30 minutes | Clients ask again; there are no refresh tokens for them |

- Banks can shorten or lengthen the idle timeout and absolute lifetime.
- Signing out, changing a password, a reset or a deactivation ends the session on the server, not
  only in the browser, and clears the browser's cache and storage for OpenClear.
- **Concurrent sessions are unlimited.** People commonly use a desktop and a phone, and a limit
  mostly locks out the real user. Every session can be ended: by the person ("Sign out other
  devices"), by an administrator for one person, and on a password change.
- Session cookies are HttpOnly and SameSite, with `__Host-` or `__Secure-` prefixes over HTTPS.

## Identity providers and their sessions

OpenClear's session is separate from the identity provider's. Signing in through single sign-on
starts an OpenClear session with OpenClear's own idle timeout and absolute lifetime, and ending
the provider's session ends OpenClear's through back-channel logout (OIDC) or single logout (SAML)
when the bank has set them up. A person signed out of OpenClear signs in again through the
provider, which may not ask for a password if its own session is still alive; that's the
provider's setting.
