# Customers managing their own people and sign-on

A business customer with dozens of people shouldn't have to ask the bank every time someone
joins, leaves or changes role, and a customer with its own identity provider (Entra ID,
Okta, Google Workspace) expects its people to sign in with it. Today only bank staff can do
either. This plan lets the bank hand both to a customer's own administrators, one customer
at a time, without giving them any way to reach beyond their own customer.

## What exists today

| Area | Today |
|---|---|
| People | Bank staff with `user:manage` add, edit, deactivate, unlock and reset people, one at a time or by CSV, at `/ui/users`. `user:manage` is masked out of every customer-scoped session (`auth/permissions.py`), so a customer can't, whatever group it's given. The CSV path already accepts a customer-scoped uploader (`create_users_from_rows(scoped_customer_id=...)`). Per-account access (1.14.0) is bank-only too. |
| Groups | Security groups belong to the bank. Any group can be given to a customer's person; the masked permissions are stripped at sign-in. |
| Sign-on | Per-customer OIDC and SAML connections already exist (`sso_connection.customer_id`), with group mappings, auto-provisioning, SCIM, back-channel logout and a per-customer "password sign-in allowed" switch. All are managed by bank staff (`customer:manage`) at `/ui/customers/{id}/sso`. |
| Safeguards already in place | An identity provider can't sign in an existing person who isn't already a member in its scope (no silent linking). An admin can't reset the password of someone who belongs to another bank. OIDC discovery refuses private addresses. |
| Customer self-service | Banner message and reminders (`customer_banner:manage`), billing statements, and the customer control panel (`web/control_panel.py`), which is where the new pages go. |

## What a customer administrator can do

**People** (a new permission, `customer_user:manage`, not masked):
- list, search and export the customer's people;
- add a person (an invitation email; a CSV of many), choosing one of the groups the bank
  offers to customers, and which of the customer's accounts they see;
- change a person's group or accounts, require a passkey, deactivate and reactivate, unlock,
  sign them out everywhere;
- reset a password, only for someone who belongs to this customer alone;
- see a log of the customer's own people and sign-on changes.

**Sign-on** (a new permission, `customer_sso:manage`, not masked):
- set up an OIDC or SAML connection to the customer's identity provider, with group
  mappings to the groups the bank offers;
- test it before relying on it;
- verify the customer's email domains, and turn on auto-provisioning for them;
- issue a SCIM token so the customer's directory adds and removes people itself;
- turn off password sign-in for the customer's people, once sign-on works.

**What stays with the bank:** turning self-service on per customer, which groups are
offered, the customer's accounts, API clients, and the power to suspend any of it at once.

## Guardrails

Handing these to a customer opens paths that don't exist while only the bank configures
them. Each needs closing before customers get them:

1. **Switching organizations after a customer's sign-on.** Switching (`/ui/switch-tenant`)
   doesn't ask for a password again. If a person belongs to customer A and also to customer
   B or the bank, customer A's identity provider could sign them in and they could switch
   into B or the bank. A session started through a customer's connection is confined to
   that customer; going anywhere else means signing in again there.
2. **Password resets and identity-wide actions.** Today's rule is "no reset if the person
   belongs to another bank". For a customer admin it must be "no reset, unlock or sign-out
   everywhere if the person has any membership outside this customer", including the bank's
   own staff. Outside that, they can only change the membership itself.
3. **Escalation through groups.** A customer admin can only give out the groups the bank
   marks "offered to customers", can't edit groups, and the masked permissions stay masked.
4. **Adding someone who already exists.** A customer admin never links an existing identity
   silently. If the email belongs to someone elsewhere, that person gets an invitation and
   accepts it themselves.
5. **Email squatting through auto-provisioning.** A customer's identity provider can assert
   any email. Auto-provisioning on a customer-managed connection only creates people whose
   email domain the customer has verified (a DNS TXT record). Unverified, only existing
   members of the customer can sign in through it.
6. **Locking everyone out.** Password sign-in can only be turned off with an active
   connection that the admin turning it off has just signed in through. Bank staff can
   always turn it back on.
7. **The last administrator.** A customer admin can't remove or demote the customer's last
   administrator; the bank can.

## Phases

| Phase | What lands | Migration |
|---|---|---|
| **CA1. Delegation and guardrails** | Per-customer switches for the bank ("manages its own people", "manages its own sign-on"), the two permissions, "offered to customers" on security groups, session confinement (guardrail 1), the customer-level reset rule (2), the last-admin rule (7). | `customer.self_manages_users`, `customer.self_manages_sso`, `security_group.offered_to_customers`, the session's confining connection |
| **CA2. People** | The customer's People page and tile: list, search, export, add by invitation and CSV, edit group and accounts, passkey requirement, deactivate, unlock, reset, sign out everywhere, invitations for existing identities (4), the scoped activity log. | `user_invitation` |
| **CA3. Sign-on** | The customer's Sign-on page and tile: OIDC and SAML set-up, group mappings limited to offered groups, a test sign-in, domain verification (5), SCIM tokens, password sign-in off with the lock-out rule (6). | `customer_email_domain`, `sso_connection.managed_by` |
| **CA4. Bank oversight** | On the bank's customer page: whether it self-manages, its administrators, recent changes, and suspend switches. A Controls entry and evidence-export section for customer-managed sign-on. Security events carry the customer. API endpoints for the same (`/api/v1/customers/{id}/...`) for the bank. | None |

All four can ship as one stage and one release (1.15.0), or CA1–CA2 first if people
management is wanted sooner.

## Decisions

| # | Question | Recommendation |
|---|---|---|
| CA-D1 | Who turns self-service on? | The bank, per customer, off by default. People and sign-on separately. |
| CA-D2 | Can customers make their own groups? | No. The bank marks which of its groups are offered to customers. Customer-defined groups within a bank-set ceiling can come later if asked for. |
| CA-D3 | Who's a customer's first administrator? | The bank appoints them. They can appoint others, if an offered group carries `customer_user:manage`. |
| CA-D4 | Should a bank approve each new customer user (maker-checker)? | Not in the first version. The bank is notified, sees every change, and can suspend. Add an approval step if a bank asks. |
| CA-D5 | Does session confinement also apply to customer connections the bank manages? | Yes, to every customer-scoped connection: simpler, and safer. |
| CA-D6 | Domain verification method | DNS TXT record only. Email-link verification proves a mailbox, not a domain. |
| CA-D7 | Can customer admins use the API for this? | Later. It needs customer-owned API clients, which are bank-only today. |
| CA-D8 | Can a customer tighten its own password policy? | Yes, never below the bank's. Moves from bank-only to the customer's Sign-on page. |

## Testing

- An escalation suite, the customer analogue of `test_cross_customer_isolation.py`: a
  customer admin tries every guardrail above and is refused (other customers' people, the
  bank's staff, a group not offered, a person in two organizations, switching after a
  customer sign-on, auto-provisioning an unverified domain, locking everyone out, removing the
  last admin).
- The existing SSO tests run again for customer-managed connections.
- One manual pass with a real identity provider as the customer's: Keycloak in a container
  for OIDC and SAML, and SCIM.
- The three suites before merging, as for every stage.

## Progress log

(Newest last.)
- 2026-10-13 Plan written. Nothing built yet.
