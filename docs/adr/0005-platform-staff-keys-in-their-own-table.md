# Platform staff's security keys live in their own table

Platform staff must always sign in with a security key (ADR 0004), but every security key in
`webauthn_credential` belongs to a bank: its `tenant_id` can't be empty, and row-level security
shows a session only its own bank's keys. Platform staff's keys therefore go in a separate
`platform_webauthn_credential` table, read only by the platform pages, with no row-level
security because it holds no bank's data.

Letting `webauthn_credential.tenant_id` be empty was rejected: its RLS policy would need a
special case for rows with no bank, and the boundary between a bank's people and the service
provider's would blur in the one table every bank's sign-in reads.
