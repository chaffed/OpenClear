# OpenClear plugin framework plan

Started 2026-10-09. Goal: let others extend OpenClear (a new payment network, a core's
file layout, an OCR service, extra matching rules) **without merging into this repo**:
`pip install openclear-<something>`, enable it, done. Tracked like the other plans:
phases on their own branches, decisions below, a progress log at the bottom.

## Two ways to extend

| | Out of process | In process (plugins) |
|---|---|---|
| How | Webhooks and the API (SDK_PLAN.md P0) | A Python package OpenClear loads at startup |
| Good for | Reacting to events, syncing a core, dashboards, anything in another language | New networks, file formats and layouts, providers, rules and scoring signals |
| Trust | Limited to what its API client is allowed | Runs with full access inside OpenClear, so only the platform operator installs it; each bank enables what it uses |
| License | Anything (it's a separate program) | **AGPL-3.0**, like OpenClear (decision P-D1) |

This plan covers the in-process kind. The out-of-process kind is SDK P0, and the two
are built side by side.

## What's already pluggable (just not from outside)

| Extension point | Interface today | Wired in by |
|---|---|---|
| Payment networks (check, ACH) | `networks.base.NetworkAdapter` | an import in `main.py` and `register_adapter()` |
| OCR | `ocr.base.OCRProvider` | an `if`/`elif` in `ocr/factory.py` |
| Email, SMS | `notifications.email.base.EmailProvider`, `notifications.sms.base.SmsProvider` | factories |
| Fraud scoring model | `ml.model.ScoringModel` | hard-coded `LogisticRegressionScoringModel` |
| File storage | `storage.LocalStorage` / `S3Storage` | a setting |
| Bulk import formats | `services.bulk_import_service.SPECS` | a dict |

## The framework

- **Discovery: Python entry points.** A plugin package declares, in its
  `pyproject.toml`:

  ```toml
  [project.entry-points."openclear.plugins"]
  rtp = "openclear_rtp:plugin"
  ```

  At startup OpenClear loads each one and calls `plugin.register(registry)`. Nothing in
  this repo changes to add one.
- **A versioned plugin API** (`openclear.plugins`): the registry, the interfaces above
  (made stable and documented), and `PLUGIN_API_VERSION`. A plugin declares the range it
  supports; one outside it is refused at startup with a clear message, not a crash at
  first use.
- **Operator control:** `OPENCLEAR_PLUGINS` lists the plugins allowed to load (default:
  none outside the built-ins), so installing a package alone doesn't activate it. Admin →
  Plugins shows what's loaded, its version and its extension points.
- **Per-bank enablement** for the extension points that are a bank's choice (a network, a
  decision-file layout, an import format): a bank turns on what it uses, and audit-logs
  it.
- **The built-ins become plugins too** (check, ACH, Tesseract, SMTP, Twilio, local and S3
  storage), registered the same way, which proves the interface is enough.

### New extension points

- **Import formats:** a core's presentment layout, or another issued-items file format.
  These register into the bulk import job (S4), so they get chunking, retries,
  savepoints, row results and back-out for free.
- **Decision and return file layouts** (needed for SDK P3): given the period's decisions,
  write the file a given core takes back. Used by the decision outbox.
- **Matching rules:** extra checks run after the network's own, which can only add
  exception types, never clear one, so a plugin can't make an item pass that the core
  rules flagged.
- **Scoring features:** extra features for the fraud model, from the item and the bank's
  data.
- **Notification channels:** beyond email and SMS (Teams, Slack, a bank's alerting
  system).
- **Later:** UI extension slots (an extra page under a menu, a panel on the exception
  page), rendered in OpenClear's own templates with the CSP intact.

### Safety

- Plugins are trusted code, like the app itself: the README says so plainly, and only
  the operator can install and allow them.
- A plugin that fails to load is skipped and reported on the Admin page and in the logs.
  The app still starts, unless the plugin was listed as required.
- Plugin calls inside the import and matching paths run inside the row's savepoint, so
  a plugin error fails that row, not the file.
- Each extension point is tested with a sample plugin, `openclear-plugin-example`, in
  `examples/`: the template others copy.

## Phases

| Phase | What | Size | Notes |
|---|---|---|---|
| PL1 | Entry-point discovery, the registry, `PLUGIN_API_VERSION`, `OPENCLEAR_PLUGINS`, the Admin → Plugins page; turn the existing seven interfaces into registered extension points with the built-ins as plugins | M | Changes no behavior; it proves the mechanism |
| PL2 | Import formats and decision-file layouts as extension points, per-bank enablement, the example plugin, and a "write a plugin" guide | M | Feeds SDK P3 (core adapters, the Fineract layout) |
| PL3 | Matching rules, scoring features and notification channels | M | Rules can add exceptions, never clear them |
| PL4 | UI extension slots | M–L | Only once a real plugin needs one |

Sequencing with SDK_PLAN.md: PL1 runs alongside SDK P0, and PL2 before SDK P3, since
the core adapters will be plugins.

## Decisions

| # | Question | Decision |
|---|---|---|
| P-D1 | Licensing for in-process plugins | **Pure AGPL-3.0** (decided 2026-10-09). A plugin loaded into OpenClear is part of the same program, so it's AGPL too, which fits the contribute-back model. Vendors who want to stay closed integrate out of process, through webhooks and the API (SDK P0), which carry no license obligation. No plugin exception. |
| P-D2 | Discovery | **Python entry points** (`openclear.plugins`) | 
| P-D3 | Activation | **Allow-listed by the operator** (`OPENCLEAR_PLUGINS`), then enabled per bank where it's a bank's choice |
| P-D4 | Compatibility | **A plugin API version with a declared supported range**; refused at startup when it doesn't match |
| P-D5 | Rules plugins | **Can add exception types, never remove one** |

## Progress log

(Newest last.)
- 2026-10-09 Plan written. Licensing decided: pure AGPL, closed vendors integrate out of process.
- 2026-10-08 **PL1 done** (branch `plugins-pl1`). The mechanism is in place, with no change in behavior.
  - Discovery is through entry points. `openclear.plugins` holds the registry, `PLUGIN_API_VERSION = 1` and `OPENCLEAR_PLUGINS`. Admin → Plugins shows what's loaded.
  - The seven existing interfaces are now extension points, and the built-ins register through them (`plugins/builtin.py`): networks, OCR, email, SMS, storage, the scoring model (`OPENCLEAR_ML_SCORING_MODEL`) and import formats.
  - A plugin's registration is all or nothing: one that fails, or claims a name already taken, leaves nothing behind.
  - Tests use fake entry points to cover these cases: allowed, not allowed, incompatible, failing to import, failing to register, and a name already taken.
  - Deferred to PL2:
    - per-bank enablement;
    - the "required plugin" setting, under which the app won't start without the plugin.
- 2026-10-08 **PL2 done** (branch `plugins-pl2`).
  - **Decision layouts are an extension point.** The built-in is `openclear_csv`, served at `GET /api/v1/decisions/file?layout=`.
  - **Banks choose which plugin entries they use.** A plugin's import formats and decision layouts are off for each bank until its admin turns them on under Admin → Plugins. The change is audited, and the setting is stored in `tenant_extension` under row-level security.
  - **Files in any format can be uploaded.** The API has `POST /api/v1/bulk-uploads` (multipart) and `GET /api/v1/bulk-uploads/formats`; the web UI has Bulk uploads → Other formats.
  - **Required plugins:** with `OPENCLEAR_PLUGINS_REQUIRED`, OpenClear refuses to start if a listed plugin doesn't load.
  - **For plugin authors:**
    - `openclear.plugins.api` is the stable set of names a plugin imports;
    - `examples/openclear-plugin-example` provides a fixed-width paid-items format and a fixed-width decision file, and is tested end to end;
    - `docs/PLUGINS.md` is the guide to writing a plugin.
  - **A new Postgres guard** checks that every table with a `tenant_id` has row-level security, apart from the five left out on purpose (`decision`, `exception_item`, `ml_model`, `job`, `notification`). Each of those is listed with the migration that explains why.
  - **Still open:**
    - per-bank enablement for networks, which waits until a network plugin exists;
    - dropbox inboxes for plugin formats;
    - a scheduled decision-file outbox, which is SDK P3.
- 2026-10-08 PL1 and PL2 merged into `main`. All three suites were green: SQLite 1325 passed, Postgres 1347, Linux 3.11 1319.
- 2026-10-08 **PL3 done** (branch `plugins-pl3`).
  - **Matching rules** are chosen per bank. A rule can only add exception codes, declared with the names people see, and can't claim a code that's built in or another plugin's. Each rule runs in a savepoint after the network's own rules. A rule that fails (it raises, or returns a code it didn't declare) raises `plugin_rule_failed` instead, so the item goes to a person. This is P-D5.
  - **Scoring features** are the operator's choice and apply to every bank, because they feed the shared model. They reach the model as `plugin:<name>:<key>`. A failing feature is skipped.
  - **Notification channels** are chosen per bank, with settings stored encrypted (`tenant_extension.config_encrypted`, migration `b7d9f1c3e5a7`). Channels get the webhook events and run as retried jobs.
  - `plugins.api` gains `source_item`, `apply_matching_rules` and `extra_features`, for network plugins.
  - The example plugin has one of each.
  - PL4 (UI extension slots) waits for a real plugin that needs one, as the plan says.
- 2026-10-09 PL3, F, P2, P3 and P4 (and the deadline webhook) merged into `main` at `d7f6bf25`. All three suites were green: SQLite 1406 passed, Postgres 1431, Linux 3.11 1393. The live Moov, Fineract, Node and .NET tests ran.
