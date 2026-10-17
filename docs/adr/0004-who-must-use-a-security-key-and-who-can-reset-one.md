# Who must use a security key, and who can reset one

A bank, or a customer for itself, can require a security key for password sign-ins (ASVS
6.3.3). Three kinds of sign-in are exempt, because the second factor belongs somewhere else:
SSO users (the bank's identity provider enforces MFA, a CUEC), embedded sessions (the
embedding host signed the user in, a CUEC), and API clients (not people). Platform staff are
never exempt, whatever the banks' settings, because one platform staff account reaches every
bank.

A user who loses their key is reset by an administrator with `user:manage` at their bank or
customer, at once and without a second person, after recording how they confirmed the person's
identity (ASVS 6.4.4). Dual control was rejected here: a decider
locked out near the cutoff costs the customer money, and the reset is caught instead by its
security event and the email to the user. A bank's only administrator can't be reset by
anyone at the bank, so platform staff can reset them, but only as a protected change under
the service provider's own dual control, with the bank told. That's the one case where
platform staff act on a bank's user without a support access grant, and it's deliberate.

## Consequences

- Adding an exemption weakens every bank that requires a security key. It needs this
  decision reopened, not just a new branch in the sign-in code.
- Platform staff reset of anyone other than a bank's only administrator goes through a
  support access grant, as everything else does.
