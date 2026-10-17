# OpenClear

Positive pay for banks and their business customers: the bank's customers tell it which
checks and ACH debits to expect, and someone decides what happens to anything that doesn't
match. This glossary names the parties, the protections around their settings, and how
OpenClear shows that it's secure.

## Language

### Parties

**Bank**:
A financial institution using OpenClear for its business customers; the top level of
isolation, whose data no other bank can see.
_Avoid_: tenant, FI, institution

**Customer**:
A bank's business customer, whose people issue checks, authorize ACH debits and decide
exceptions on its accounts.
_Avoid_: client, company, account holder

**Service provider**:
Whoever runs an OpenClear installation for its banks; sometimes a bank running it for
itself.
_Avoid_: operator, host, platform, vendor

**Platform staff**:
The service provider's people; they reach a bank's data only through a support access grant
the bank makes.
_Avoid_: admin, superuser, operator

**Embedding host**:
A bank's online banking site that shows OpenClear inside its own pages and has already signed
the user in.
_Avoid_: host (alone), parent site, portal

**API client**:
A system, not a person, that calls OpenClear's API with its own credentials on behalf of a
bank or customer.
_Avoid_: integration, service account, API user

### Signing in

**Security key**:
A WebAuthn credential used as a second factor: a hardware key, a passkey, or an authenticator
built into a device.
_Avoid_: passkey (as the general term), MFA device, token

**Grace period**:
The days after a bank or customer starts requiring a security key during which a user without
one can still sign in, with a warning.

**Security key reset**:
Removing all of a user's security keys so that they must register a new one at their next
sign-in.
_Avoid_: MFA reset, unlock (which is for a password lockout)

### Settings changes

**Protected change**:
A settings change that needs a second person before it takes effect.
_Avoid_: approval, maker-checker change

**Change request**:
A protected change that is waiting for its second person.

**Approver**:
Someone other than the requester who could have made the change themselves.

**Loosening**:
A change that weakens a protection, widens who may do what, or sends data somewhere new.
_Avoid_: downgrade, relaxation

**Tightening**:
A change that turns a protection on, revokes, deactivates or narrows.

### Assurance

**Requirement**:
Something an outside standard (ASVS, Nacha, FFIEC) says software or its users must do.
_Avoid_: control, rule

**Control**:
Something OpenClear itself does to meet a requirement, with a check that shows whether it is
working.
_Avoid_: requirement, safeguard

**CUEC**:
Something OpenClear relies on a bank, a customer or the service provider to do, so that its
own controls hold.
_Avoid_: customer responsibility, shared responsibility

**Advisory**:
A private record of a vulnerability in OpenClear, kept until a fix is released and then
published.
_Avoid_: security issue, bug (for an exploitable finding)
