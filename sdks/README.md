# OpenClear SDKs

These are client libraries for OpenClear's API, for banking cores, online banking providers and a bank's own systems.

| SDK | Package | Directory |
|---|---|---|
| Python 3.11+ | `openclear-sdk` | [python/](python) |
| TypeScript / JavaScript | `@openclear/sdk` | [typescript/](typescript) |
| Embedding (browser) | `@openclear/embed` | [embed/](embed) |
| .NET 8 | `OpenClear.Sdk` | [dotnet/](dotnet) |

The SDKs are licensed **Apache-2.0**, unlike the rest of this repository (AGPL-3.0). That means a proprietary core or online banking platform can ship them. The plan is to publish each from its own repository.

Each SDK has two parts:

- **Generated code.** `scripts/generate_sdks.py` generates each SDK's models and endpoint methods from the published API contract, `openapi/openclear-v1.json`. A test fails when the generated code is out of date. After an API change, regenerate it with:

  ```sh
  python scripts/export_openapi.py && python scripts/generate_sdks.py
  ```

- **A hand-written layer** with these features:
  - tokens;
  - retries with idempotency keys;
  - paging;
  - bulk import with a background-import fallback;
  - decision-feed consumption;
  - webhook verification;
  - acting for a person;
  - typed errors.

Both SDKs are tested against a running OpenClear by `tests/test_sdk/`.
