# A person is their UUID; their email can change

Every record about a person (memberships, the action log, sessions, security keys) refers to
`user.id`, a UUID. The email is what people type to sign in, and it can change: companies
rename and their addresses change with them. A change goes through one of four routes, each
emailing both the old and the new address:

- **The person,** after a recent sign-in, confirmed by a link to the new address.
- **An administrator,** for someone in their organization only, also confirmed by the new address.
- **The bank's directory (SCIM),** applied directly, as the bank's source of truth.
- **SSO,** when the provider's known subject arrives with a new email, but only if the new
  domain is verified for that bank or customer.

A separate username to sign in with was rejected. It would add something to remember and
change every sign-in path, while the actual problem (addresses changing) is solved by letting
the email change.

The SSO route is limited to verified domains because SSO sign-ins can't otherwise tell a
renamed person from an address given to someone new: that case is still refused (1.22.1,
ASVS 10.5.2).
