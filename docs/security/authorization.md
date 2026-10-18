# Authorization

What decides what someone may do and see in OpenClear. It answers ASVS 5.0 8.1.1–8.1.4. Terms
are as in [CONTEXT.md](../../CONTEXT.md).

## Functions: permissions in security groups

Every page, API call and background job checks a named permission (`auth/permissions.py`), such
as `exception:decide`, `issued_item:write` or `user:manage`. A person or API client has the
permissions of the security group on their membership, read fresh on every request, so a change
to the group, or a deactivation, applies at once.

- **Segregation of duties:** permissions that conflict can't be combined in one group when the
  bank enforces it (OC-AC-05).
- **Dual control:** a decision can need a second person, and settings that weaken a protection
  need a second administrator (OC-AC-06, OC-AC-07).
- **Customer-scoped sessions** never have certain bank-only permissions, whatever their group
  (`CUSTOMER_SCOPE_MASKED_PERMISSIONS`).

## Data: bank, customer and account scope

| Scope | Enforced by |
|---|---|
| A bank's data from every other bank's | Every query runs as one bank; on Postgres, row-level security (forced on each table) refuses rows of any other bank (`db/tenancy.py`). The app must connect as a role that isn't a superuser, which can't bypass it: the service provider's part. |
| A customer's data from other customers' | Customer-scoped repositories on every query (`repositories/`) |
| Some of a customer's accounts | Per-account access for a person or API client (`db/account_scope.py`) |
| Platform staff | Only through a time-limited grant the bank makes (OC-AC-08) |

A request for something outside these scopes gets "not found", not "forbidden", so it doesn't
reveal that it exists.

## Fields

OpenClear has no per-field permissions. What someone sees of a record follows from whether they
may see the record at all: someone who can see an account sees its full number. Writes accept only
the fields each API schema lists, so a request can't set a field it wasn't meant to (ASVS 15.3.3).

## Context

Authorization decisions don't use the time of day, location, IP address or device; ASVS 8.1.3,
8.1.4 and 8.2.4 are recorded as not met at Level 3, with this reason: OpenClear sits behind each
bank's edge and identity provider, which are better placed for those rules. The client's IP
address is used for rate limits and recorded in the action log and security events, and new-device
sign-ins are emailed to the person (from 1.24).
