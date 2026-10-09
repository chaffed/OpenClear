# Writing an OpenClear plugin

A plugin adds to OpenClear without changing its code. It's a separate Python package that OpenClear finds when it starts.

The working example in [examples/openclear-plugin-example](../examples/openclear-plugin-example) is the template to copy. It adds a fixed-width import format and a fixed-width decision file.

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

Matching rules, scoring features and notification channels come in PL3 (see [PLUGIN_PLAN.md](../PLUGIN_PLAN.md)).

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

`DecisionLayout(label, media_type, extension, write)` describes a layout:

- `write` receives decisions as `GET /api/v1/decisions` returns them, oldest first.
- It returns the file's bytes.

A core fetches the file with `GET /api/v1/decisions/file?layout=<name>&after=<last sequence>`. The `X-Last-Sequence` header gives the sequence to acknowledge once the core has acted on the file.

## Turning a plugin on

1. **The operator installs and allows the plugin.** Installing the package isn't enough; set `OPENCLEAR_PLUGINS=yourcore`. Use `OPENCLEAR_PLUGINS_REQUIRED=yourcore` instead if OpenClear shouldn't start without the plugin.
2. **Each bank turns on what it uses.** A bank admin turns on the plugin's import formats and decision layouts under **Admin → Plugins**. The change is recorded in the bank's action log. OpenClear's own formats and layouts are always on.

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
