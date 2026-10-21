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
| Import format | `add_import_format(kind, name, ImportSpec)` | Each bank, under Settings → Plugins |
| Decision file layout | `add_decision_layout(name, DecisionLayout)` | Each bank, under Settings → Plugins |
| Matching rule | `add_matching_rule(MatchingRule)` | Each bank, under Settings → Plugins |
| Notification channel | `add_notification_channel(NotificationChannel)` | Each bank, with its own settings |
| Scoring feature | `add_scoring_feature(ScoringFeature)` | The operator (it applies to every bank) |
| Page addition (UI slot) | `add_ui_slot(UiSlot)` | Each bank, under Settings → Plugins |
| The plugin's own routes | `add_web_router(router)` | Answer only for banks that turned on one of its UI slots |
| The plugin's static files | `add_static(directory)` | Served at `/static/plugins/<plugin>/` |

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

Features apply to every bank. They're installed by the operator, and each bank's own model is trained on them.

A network plugin's own adapter should end `evaluate` with `apply_matching_rules(...)` and add `extra_features(...)` to what `build_features` returns. That way, rule and feature plugins apply to the new network as well. The built-in networks already do this.

## Notification channels

A `NotificationChannel(name, label, send, config_fields, events)` sends a bank's events somewhere: a chat tool, a paging service, the bank's alerting system.

- **Settings:** each bank turns the channel on and fills in its `config_fields`, such as a webhook URL or a token. Fields marked `secret` are stored encrypted and never shown again.
- **Which events:** the same ones as webhooks (`exception.created`, `exception.decided`, `bulk_upload.completed`), or only the ones listed in `events`.
- **What `send(event, config)` receives:** the event's `type`, `timestamp`, `data`, `bank` and `link` (a URL to the exception, when `OPENCLEAR_PUBLIC_BASE_URL` is set), plus the bank's settings.
- **How it runs:** as a background job. If `send` raises an error, the job is retried.

A channel that calls out over the network is making requests on behalf of every bank that uses it, so validate the URLs it accepts.

## Page additions (UI slots)

A `UiSlot(name, slot, render, permission=None, label="", scripts=(), styles=())` adds to one of
OpenClear's pages. `render(context)` returns a `SlotContent(title, html, actions, state)`, or
`None` to show nothing.

| Slot | Kind | On a desktop | On a phone |
|---|---|---|---|
| `exceptions.toolbar`, `issued_items.toolbar` | toolbar | Buttons under the page's heading | Behind one "More" button, in a sheet |
| `exception.row.action`, `paid_item.row.action` | row action | Links in the row | Links on the card |
| `exception.detail.panel`, `customer.detail.panel`, `dashboard.panel` | panel | A card on the page | Closed until opened |
| `nav.page` | page | A menu entry and its page, at `/ui/plugin-pages/<name>` | A drawer entry |
| `settings.tile` | tile | A control-panel tile under Integrations | A tile |

- **What `render` gets:** `context.ctx`, the person's TenantContext (bank, person, customer,
  permissions); `context.session`, a database session of its own bound to the same scope, so a
  customer's person reaches only their customer's data and only the accounts they're limited to;
  and `context.values`, the plain values the page passed (an exception's page passes
  `exception_id` and `network`; a customer's page, `customer_id`; a row, its item's id). Read
  through it with `get_exception(session, ctx, id)` and `source_item(session, item)`.
- **What it returns:** `html` is cleaned to a short list of tags (paragraphs, lists, tables,
  links, `strong`, `code` and the like) with only `class`, `title` and `href` kept: no scripts,
  styles or event handlers, so the Content Security Policy holds. The title and action labels
  are text. Each `SlotAction(label, href, method="get")` must point at a path on this site,
  normally one of the plugin's own routes; a `"post"` action is sent as a form with the page's
  CSRF token. For a tile, `html` is its description, `state` what shows beside its name, and
  its first action where it leads. For a page, `render` gets the query string in `values`.
- **Never breaking a page:** `render` runs in a worker thread and has 200 ms (a whole page, 2 s).
  One that raises, takes longer or returns something malformed is skipped and logged as
  `plugin.slot.failed`, and the page goes on without it. Don't do slow work in `render`: keep
  what a vendor tells you, and show that.
- **JavaScript and CSS:** ship them as files in the plugin's static folder (`add_static`) and
  name them in `scripts` and `styles`; they're loaded only on pages where the slot rendered.
- **Its own routes:** an APIRouter registered with `add_web_router` is mounted at
  `/ui/plugins/<plugin>/`, and answers only for a bank that turned on at least one of the
  plugin's UI slots. Use the dependencies in `openclear.plugins.api`:
  `Depends(require_web_permission("exception:read"))` (or `Depends(web_context)`) for the
  person, `Depends(verify_csrf)` on a form post, `Depends(database)` for the session;
  `render_page(request, ctx, title=..., html=...)` for a page in OpenClear's layout; and
  `record_action(session, ctx, action=..., summary=...)` before you commit anything.

The example plugin fills one slot of each kind
([ui.py](../examples/openclear-plugin-example/src/openclear_plugin_example/ui.py)).

## Turning a plugin on

1. **The operator installs and allows the plugin.** Installing the package isn't enough; set `OPENCLEAR_PLUGINS=yourcore`. Use `OPENCLEAR_PLUGINS_REQUIRED=yourcore` instead if OpenClear shouldn't start without the plugin.
2. **Each bank turns on what it uses.** A bank admin turns on the plugin's import formats, decision layouts, matching rules, notification channels and page additions under **Settings → Plugins**. The change is recorded in the bank's action log. OpenClear's own entries are always on.

A plugin is skipped if it:

- targets another plugin API version;
- fails to import;
- raises in `register()`;
- claims a name that's already taken.

A skipped plugin leaves nothing registered. It's reported under Settings → Plugins and in the log, and OpenClear still starts.

## Testing

Test a plugin against OpenClear's own test fixtures. [tests/test_auth/test_plugins_bank_choices.py](../tests/test_auth/test_plugins_bank_choices.py) does this for the example plugin. It loads the plugin through a fake entry point, uploads a file, and reads a decision file. [tests/test_web/test_plugin_slots.py](../tests/test_web/test_plugin_slots.py) does the same for its page additions, including a slot that fails, one that's slow and one that returns unsafe HTML.

## Writing a safe plugin

A plugin runs with OpenClear's own access, so a mistake in it is a mistake in OpenClear. The
rules OpenClear's own code follows (the [ASVS crosswalk](compliance/asvs.md) shows where each is
proven) apply to it:

- **Stay inside the bank and the person.** Read through the session you're given
  (`context.session`, `Depends(database)`): it's bound to the person's bank and, for a customer's
  person, their customer and accounts, and on Postgres row-level security enforces that. Never
  open a session of your own, and never look up a row by an id from a URL or form without that
  scope (fetch it through the session, or `get_exception`), or one bank's person can read another's.
- **Check permissions and CSRF on your routes.** Every route takes
  `require_web_permission(...)` for the narrowest permission that fits, and every form post
  `Depends(verify_csrf)`. A route that changes something calls `record_action` before it commits,
  so it's in the bank's action log.
- **Keep sensitive values out of URLs and logs.** No account numbers, tokens or secrets in a
  path or query string (they reach access logs and browser history), and none in log lines or
  `record_action` summaries. Mask account numbers you show to the last four digits where the full
  number isn't needed.
- **Keep secrets encrypted.** A vendor's API key or password goes in the database through
  `openclear.auth.crypto.encrypt_secret`, never in plain text, and is shown to no one after it's set.
- **Call outside services safely.** Use `openclear.auth.outbound_http.public_only_client(timeout=...)`
  for HTTP: it refuses private and internal addresses (so a URL a bank types can't reach your
  network) and checks TLS; always pass a timeout. Treat what comes back as untrusted.
- **Parse files defensively.** An import format gets a file someone uploaded: limit how much you
  read, refuse what you can't parse rather than guessing, and parse XML with external entities
  and network access turned off. OpenClear has already checked the upload for malware when a
  scanner is configured; a file your plugin fetches itself, check with
  `openclear.services.malware_service.check_bytes`.
- **Show only cleaned HTML.** A slot's HTML is cleaned for you; a page of your own
  (`render_page`) escapes what you put in it only if you build it with Jinja or `html.escape`, so
  do. No inline scripts: the Content Security Policy blocks them.
- **Pin your dependencies.** Publish your plugin with its own dependencies pinned, and keep them
  current: the operator's installation inherits their vulnerabilities.
- **Test the refusals.** Besides what your plugin does, test that another bank's person, a
  customer's person and someone without the permission are refused, as
  [test_plugin_slots.py](../tests/test_web/test_plugin_slots.py) does.

## Trust and licensing

A plugin runs inside OpenClear with its full access to every bank's data, so only install plugins you trust as much as OpenClear itself.

A plugin loaded into OpenClear is part of the same program, so it's AGPL-3.0-or-later too. If you'd rather keep your code closed, integrate from outside through the API and webhooks, which carry no license obligation.
