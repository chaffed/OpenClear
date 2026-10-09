# OpenClear developer sandbox

This is a public, free, self-service sandbox for anyone building on OpenClear (SDK_PLAN.md P4). Vendors create their own sandbox bank at `/ui/sandbox`, build against it, and check their work with the integration checklist (`docs/guides/certification.md`).

The stack (`docker-compose.yml`):

| Service | What |
|---|---|
| openclear, worker | OpenClear with `OPENCLEAR_SANDBOX_SIGNUP_ENABLED=true`, plus its scheduler: housekeeping (which wipes expired sandboxes), the decision outbox and default dispositions |
| postgres | OpenClear's database |
| moov-ach, moov-icl | Moov's ACH and image cash letter servers, used by `scripts/sandbox_test_files.py` to build NACHA and X9.37 test files |
| fineract, fineract-db | An Apache Fineract core |
| fineract-adapter | The reference adapter (`integrations/fineract`), every 5 minutes. It syncs Fineract's clients and savings accounts into a sandbox bank, sends checks paid from them as presented items, and undoes the withdrawal for a returned check |

## Running it

```sh
python scripts/generate_keys.py --output-dir sandbox/keys
cp sandbox/.env.example sandbox/.env        # set PUBLIC_BASE_URL, passwords, SSO_ENCRYPTION_KEY
docker build -t openclear:latest .
docker compose -f sandbox/docker-compose.yml --env-file sandbox/.env up -d
```

Put it behind TLS at `PUBLIC_BASE_URL`. Embedding and SAML need HTTPS.

**On an arm64 host,** Moov's image cash letter server is published for amd64 only, and its Go runtime crashes under emulation. Build it natively instead:

```sh
docker build -t openclear/moov-icl-arm64 sandbox/moov-icl
```

Then set `MOOV_ICL_IMAGE=openclear/moov-icl-arm64` in `.env`.

**Fineract's demo credentials** are `mifos` / `password` on tenant `default`. To connect the adapter to a sandbox bank, create a sandbox, create an API client in it, and set `ADAPTER_CLIENT_ID` and `ADAPTER_CLIENT_SECRET`.

## Things to know

- **Rules for vendors:** sandboxes are for synthetic data only, and every sign-up accepts that. Each sandbox is wiped after `OPENCLEAR_SANDBOX_LIFETIME_DAYS`.
- **Rate limit:** sign-ups are limited per address (`OPENCLEAR_SANDBOX_SIGNUPS_PER_HOUR`).
- **Never enable sandbox sign-up on an installation with real banks.**
