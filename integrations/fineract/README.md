# openclear-fineract

This is a reference adapter between [Apache Fineract](https://fineract.apache.org/) and OpenClear. It's built on the OpenClear Python SDK and licensed Apache-2.0. Use it as it is, or as the pattern for your own core.

```sh
pip install -e sdks/python -e integrations/fineract
export OPENCLEAR_URL=... OPENCLEAR_CLIENT_ID=... OPENCLEAR_CLIENT_SECRET=...
export FINERACT_URL=https://fineract.example/fineract-provider/api/v1 FINERACT_USERNAME=mifos FINERACT_PASSWORD=... FINERACT_TENANT=default
openclear-fineract run      # every few minutes, from cron
```

Each `run` does three steps. You can also run them one at a time with `sync`, `presented` and `decisions`.

| Step | Fineract | OpenClear |
|---|---|---|
| sync | clients, savings accounts | customers (by external id or account number) and accounts (by account number), upserted |
| presented | savings withdrawals paid by **Check** (payment type) with a check number | presented items, matched against the issued-check file |
| decisions | returned checks: the withdrawal is **undone** (`?command=undo`) | each decision is acknowledged as consumer `fineract` |

Things to know:

- **Permissions:** the OpenClear API client needs a security group that can write customers, accounts and paid items, and read exceptions.
- **State:** the adapter keeps its state, including which Fineract transaction each check came from, in `openclear-fineract.sqlite`. Back it up with your other job state.
- **Unfinished work** is listed under `skipped` in the run's report, and the exit status is 1. For example, an account that isn't synced yet, or a returned check it has no transaction for.
