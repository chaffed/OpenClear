# Writing an OpenClear plugin

A plugin adds to OpenClear without changing its code. It's a separate Python package that OpenClear finds when it starts.

The working example in [examples/openclear-plugin-example](../examples/openclear-plugin-example) is the template to copy. It adds one of each kind of extension a bank chooses:

- a fixed-width import format;
- a fixed-width decision file;
- a matching rule;
- a scoring feature;
- a notification channel.

## What a plugin can add

| Extension point | `registry.` method | Who turns it on |
|---|---|---|
| Payment network | `add_network(adapter)` | The operator |
| OCR provider | `add_ocr_provider(name, factory)` | The operator (`OPENCLEAR_OCR_PROVIDER`) |
| Email or SMS provider | `add_email_provider` / `add_sms_provider` | The operator |
| Storage backend | `add_storage_backend(name, factory)` | The operator (`OPENCLEAR_STORAGE_BACKEND`) |
| Scoring model | `add_scoring_model(name, factory)` | The operator (`OPENCLEAR_ML_SCORING_MODEL`) |
| Import format | `add_import_format(kind, name, ImportSpec)` | Each bank, under Admin → Plugins |
| Decision file layout | `add_decision_layout(name, DecisionLayout)` | Each bank, under Admin → Plugins |
| Matching rule | `add_matching_rule(MatchingRule)` | Each bank, under Admin → Plugins |
| Notification channel | `add_notification_channel(NotificationChannel)` | Each bank, with its own settings |
| Scoring feature | `add_scoring_feature(ScoringFeature)` | The operator (it applies to every bank) |

Extension slots in the web UI are planned as PL4, once a real plugin needs one (see [PLUGIN_PLAN.md](../PLUGIN_PLAN.md)).

## The package

```toml
# pyproject.toml
[project]
name = "openclear-plugin-yourcore"
license = "AGPL-3.0-or-later"
dependencies = ["openclear"]

[project.entry-points."openclear.plugins"]
yourcore = "openclear_plugin_yourcore:plugin"
```

The entry point names an object with four attributes:

```python
class _Plugin:
    name = "yourcore"
    version = "0.1.0"
    plugin_api = (1, 1)  # the plugin API versions it supports, lowest and highest

    def register(self, registry) -> None:
        registry.add_decision_layout("yourcore_returns", DecisionLayout(...))

plugin = _Plugin()
```

Import only from `openclear.plugins.api`. Those names stay stable within a plugin API version. Anything else in OpenClear can change in any release.

## Import formats

An `ImportSpec` has two parts:

- **`load(ref, filename) -> LoadedFile`** reads the whole file once.
  - Read the file with `read_stored_file(ref)`.
  - Return one `SourceRow(label, data)` per item. The label is what a person sees in the results, such as "line 12".
  - Raise `BulkFileRejected("...")` if the file as a whole can't be read. Nothing is imported, and the message is shown.
- **`ingest(ctx, row, loaded) -> uuid`** imports one row and returns the id it created.
  - Each row runs in its own savepoint. If it raises, that row fails with the message, and the rest of the file carries on.

Most core formats only differ in layout. In that case, parse each line into the columns a built-in format expects, and reuse that format's `ingest`. Get it with `builtin_import_format(BulkUploadKind.PAID_ITEMS, "tabular")`. Lookups, matching, exceptions, notifications, row results and back-out then all work unchanged. The example plugin does this.

A bank uploads a file in a plugin's format in one of two ways:

- **Bulk uploads → Other formats** in the web UI;
- `POST /api/v1/bulk-uploads` (multipart), with `kind` and `format`.

`GET /api/v1/bulk-uploads/formats` lists the formats a bank can use.

## Decision file layouts

`DecisionLayout(label, media_type, extension, write, config_fields=())` describes a layout:

- `write(feed, settings)` receives decisions as `GET /api/v1/decisions` returns them, oldest first.
- It also receives the bank's settings for the layout: the `config_fields` it declares, filled in under **Settings → Decision files**.
- It returns the file's bytes.
- The outbox writes it on the bank's schedule.

A core fetches the file with `GET /api/v1/decisions/file?layout=<name>&after=<last sequence>`. The `X-Last-Sequence` header gives the sequence to acknowledge once the core has acted on the file.

## Matching rules

A `MatchingRule(name, network, exception_types, check)` adds a check to a network's matching:

- **What `check` receives:** the session and the item: a `PaidItem` for `"check"` and an `AchTransaction` for `"ach"`.
- **What `check` returns:** the exception codes to add, taken from the `exception_types` the rule declared (code → the name people see), or nothing.
- **When it runs:** after the network's own rules, in a savepoint.

A rule can only **add** exception types. It can never clear one, so it can't let through an item that OpenClear's own rules flagged.

If `check` raises an error, or returns a code it didn't declare, the item gets the `plugin_rule_failed` exception. A broken rule therefore sends items to a person rather than letting them through.

A rule can't declare a code that OpenClear or another plugin already uses.

## Scoring features

A `ScoringFeature(name, network, build)` adds inputs to the fraud model:

- **What `build` receives:** the session and the exception. Call `source_item(session, exception_item)` to get the check or ACH debit it's about.
- **What `build` returns:** numbers, booleans or short strings, keyed by name.
- **How they reach the model:** as `plugin:<name>:<key>`.

A feature that raises is left out and logged, and scoring carries on.

Features apply to every bank. The shared model is trained on every participating bank's decisions, so its inputs have to be the same for all of them.

A network plugin's own adapter should end `evaluate` with `apply_matching_rules(...)` and add `extra_features(...)` to what `build_features` returns. That way, rule and feature plugins apply to the new network as well. The built-in networks already do this.

## Notification channels

A `NotificationChannel(name, label, send, config_fields, events)` sends a bank's events somewhere: a chat tool, a paging service, the bank's alerting system.

- **Settings:** each bank turns the channel on and fills in its `config_fields`, such as a webhook URL or a token. Fields marked `secret` are stored encrypted and never shown again.
- **Which events:** the same ones as webhooks (`exception.created`, `exception.decided`, `bulk_upload.completed`), or only the ones listed in `events`.
- **What `send(event, config)` receives:** the event's `type`, `timestamp`, `data`, `bank` and `link` (a URL to the exception, when `OPENCLEAR_PUBLIC_BASE_URL` is set), plus the bank's settings.
- **How it runs:** as a background job. If `send` raises an error, the job is retried.

A channel that calls out over the network is making requests on behalf of every bank that uses it, so validate the URLs it accepts.

## Turning a plugin on

1. **The operator installs and allows the plugin.** Installing the package isn't enough; set `OPENCLEAR_PLUGINS=yourcore`. Use `OPENCLEAR_PLUGINS_REQUIRED=yourcore` instead if OpenClear shouldn't start without the plugin.
2. **Each bank turns on what it uses.** A bank admin turns on the plugin's import formats, decision layouts, matching rules and notification channels under **Admin → Plugins**. The change is recorded in the bank's action log. OpenClear's own entries are always on.

A plugin is skipped if it:

- targets another plugin API version;
- fails to import;
- raises in `register()`;
- claims a name that's already taken.

A skipped plugin leaves nothing registered. It's reported under Admin → Plugins and in the log, and OpenClear still starts.

## Testing

Test a plugin against OpenClear's own test fixtures. [tests/test_auth/test_plugins_bank_choices.py](../tests/test_auth/test_plugins_bank_choices.py) does this for the example plugin. It loads the plugin through a fake entry point, uploads a file, and reads a decision file.

## Trust and licensing

A plugin runs inside OpenClear with its full access to every bank's data, so only install plugins you trust as much as OpenClear itself.

A plugin loaded into OpenClear is part of the same program, so it's AGPL-3.0-or-later too. If you'd rather keep your code closed, integrate from outside through the API and webhooks, which carry no license obligation.
