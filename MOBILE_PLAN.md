# Phones and tablets, plugin UI slots, and two bank-requested features

OpenClear's decision work (the exception queue, Pay/Return, the action log) has to work on a
phone and a tablet (320–1023px) as well as it does on a desktop, without changing the desktop.
This plan also builds plugin UI slots (PLUGIN_PLAN.md PL4), customer-owned API clients
(CUSTOMER_ADMIN_PLAN.md CA-D7) and the bank approval of new customer users (CA-D4, off by
default), and scopes edge mTLS pinning without showing it to banks.

## What exists today

| Area | Today |
|---|---|
| Stack | Server-rendered Jinja, vanilla JS (`static/js/*.js`, no build step), one stylesheet (`static/css/app.css`, ~650 lines). A strict Content Security Policy: no inline scripts. Keep it; no framework. |
| Rails | Check and ACH only (`networks/check`, `networks/ach`, `networks/registry.py`). No FedNow/RTP; designs stay rail-agnostic. |
| Phones already | M3 (1.10.0): `table.exception-table` turns into cards below 720px with `td[data-label]`; a sticky `.decision-card`; Pay/Return buttons; P/R/J/K keys. Breakpoints are ad hoc: 640px and 720px. |
| Shared components | Confirm dialog (`data-confirm`), toasts, busy states, polling (M2), control-panel modals (M4). |
| Controls | Dual control (maker/checker) and segregation of duties are enforced on the server; the UI only has to keep its confirmations usable on a phone. |
| Tests | Playwright + axe browser tests (`tests/fixtures/axe/axe.min.js`, the harness's `serve()`), three full suites (CLAUDE.md). |
| Plugins | `plugins/` (api.py, builtin.py): import formats, notification channels, rules. No UI slots (PL4 deferred until needed; now decided: build). |

## Decisions

| # | Question | Decision |
|---|---|---|
| MB-D1 | Swipe to Pay/Return on phones? | **No** (decided 2026-10-13). Too easy to trigger while scrolling, no keyboard or screen-reader equivalent, and Return needs a reason. Tap instead: Pay is one tap with a short Undo toast (or the confirmation where the bank requires one); Return opens a reason sheet. |
| MB-D2 | Edge mTLS pinning screen? | **Scoped, hidden** (decided 2026-10-13). Designed below; built only when a bank asks. Nothing about it shows to banks: no tile, no control, no docs page. |
| MB-D3 | Plugin UI slots now? | **Yes, build them** (decided 2026-10-13), with an example plugin filling every kind of slot. |
| MB-D4 | Framework? | No. Jinja macros, CSS and small vanilla JS modules, as today. |
| MB-D5 | CSS direction | Desktop-first `max-width` queries, so desktop rules are never edited; breakpoints defined once. |
| MB-D6 | Customer API clients' scope | As 1.15.0's self-service: a bank switch per customer, the customer's own scope only, roles the bank offers, optional per-account limits, read-only option, key pairs, SIEM events. |
| MB-D7 | Bank approval of customer users | A per-bank setting with a per-customer override, off by default. |

## 1. Breakpoints and layout system

| Name | Width | Layout |
|---|---|---|
| phone | < 640px | One column. Lists become cards. Navigation in a drawer. A sticky action bar at the bottom. |
| tablet | 640–1023px | Key columns stay; the rest fold into an expandable row. On landscape tablets, list and detail side by side where useful (the exception queue). |
| desktop | ≥ 1024px | Unchanged. |

- Below 1024px: touch targets at least 44×44px; no horizontal page scroll (tables that must stay tables scroll inside `.table-scroll`).
- The existing 640/720px queries move onto the two breakpoints above.

**Table → card.** One Jinja macro, `responsive_table(columns, rows)` (`templates/_macros/responsive_table.html`),
renders a normal `<table class="responsive">`. Each column declares a role:

| Role | On a phone card |
|---|---|
| `primary` | The card's title |
| `money` | Right-aligned, bold, beside the title |
| `status` | A badge |
| `meta` | A "label: value" line |
| `actions` | A footer row of full-width buttons, or a ⋯ action sheet when there are more than two |
| `hide-phone` / `hide-tablet` | Dropped below that width |

It stays one real table (screen readers, sorting, CSV export, the server-side list filters from
M2 all keep working); CSS does the conversion, generalising the `exception-table` rules.

**New components** (each a small macro plus CSS, JS only where needed):
- `nav_drawer`: the main navigation behind a menu button below 1024px.
- `bottom_sheet`: filters, the Return reason list, and action menus on phones (a `<dialog>`,
  so the focus trap and Escape come with it).
- `undo_toast`: Pay's undo, built on the existing toast.

**Pay on a phone.** One tap posts the decision as today; the toast offers Undo for 8 seconds.
Undo isn't a new kind of action: where the decision is final it's refused (the toast doesn't
show), where it's a recommendation awaiting approval it withdraws it. (There was no withdraw
path after all; R2 added `decision_service.withdraw_recommendation`: its maker only, before
approval.) Banks
requiring a confirmation (dual control's checker step, segregation of duties) get the existing
confirm dialog, sized for a phone.

## 2. Screens

**Dashboard.** Tiles stack (to decide, due today, recent uploads, controls for admins); the
"Decide now" banner stays first; the work queue is the first card list.

**Exception queue.** `responsive_table` cards: item, amount, reason badges, deadline countdown,
ML score badge. Filters in a bottom sheet. Bulk select keeps working (a checkbox on each card,
the existing bulk bar sticky at the bottom). Landscape tablet: queue on the left, the item on
the right (loaded into the pane, URL updated, back button works).

**Exception item.** Order on a phone: check image (pinch zoom, front/back toggle), issued vs
presented side by side, reasons, then the sticky decision bar holding the ML confidence score
next to Pay and Return, so the score is always visible and never covers the buttons. Below it,
collapsible: the action log (newest entry visible when collapsed), notes, plugin panels.

**Other lists.** Issued checks, presented checks, stop payments, ACH transactions, ACH rules,
accounts, customers, the action log, Users, People, API clients, bulk uploads: all move to
`responsive_table`, one per commit.

**Control panel and settings.** Tiles one per row; the settings modals become full-screen sheets.

**Customer API clients (new, R4).** The customer's Settings → Integrations: list (name, client
id, role, accounts, last used, read-only and key-pair badges), create (secret shown once, or a
public key), rotate, switch to a key pair, revoke. Same pages as the bank's own API clients,
pointed at the customer's routes.
- Permission `customer_api_client:manage`, masked unless the bank turns on
  `customer.self_manages_api_clients`; added to `CUSTOMER_SELF_SERVICE_PERMISSIONS`.
- A client's membership is the customer's, with an offered role only; optional per-account
  limits (1.14.0); read-only option; `private_key_jwt` (1.14.0).
- Never `can_act_for_users` or a sign-on connection link (bank-only features).
- Actions recorded against the customer (its activity page) and mapped to the existing
  `api_client.*` security events. The bank's API clients page lists customers' clients too,
  marked, and can revoke them.

**Bank approval of new customer users (R6, off by default).**
- `tenant.approve_customer_users` (bool), `customer.approve_users` (null = follow the bank).
- When on: accepting an invitation, or a customer-managed sign-on or SCIM creating someone,
  creates the membership inactive with `pending_approval_at`; the person sees "waiting for your
  bank to approve" on sign-in.
- Bank: Approvals → New customer users (cards on a phone): approve or reject, with a reason;
  the customer's administrators are told. Recorded in the action log and as security events
  (`user.approved`, `user.rejected`).
- Not affected: people the bank adds itself.

**Edge mTLS pinning (scoped only, hidden; MB-D2).** What it would be, for when a bank asks:
- The edge (Cloudflare Authenticated Origin Pulls, Akamai mTLS origin) presents a client
  certificate to the origin's TLS terminator (Fly, a load balancer, nginx). That layer verifies
  it; the app can't, since TLS ends before it.
- The app's part: a setting with the expected certificate fingerprint(s), compared with a
  header the terminator sets (e.g. `X-Client-Cert-Fingerprint`), refusing other requests with
  `edge.mtls.refused` events; a Controls entry; a section in `docs/guides/edge.md` per edge.
- Configuration only (environment settings, like the edge secret in WAF_PLAN.md), no bank-facing
  screen, unless the bank wants to rotate fingerprints itself.
- Effort when asked: small (S), plus testing against the bank's actual edge.

## 3. Plugin UI slots (R5; PLUGIN_PLAN.md PL4)

**Registry.** A plugin declares slots in its manifest:
```python
ui_slots = {
    "exception.detail.panel": render_vendor_panel,     # (context) -> SlotContent | None
    "exception.row.action": vendor_action,
}
```
`plugins/ui_slots.py` keeps the registry; templates call `{{ slot("exception.detail.panel", item=item) }}`.

**Slot kinds and their phone behaviour:**

| Kind | Example slots | Desktop | Phone |
|---|---|---|---|
| `toolbar` | `exceptions.toolbar`, `issued_items.toolbar` | Buttons in the page toolbar | Folded into the ⋯ menu |
| `row-action` | `exception.row.action`, `paid_item.row.action` | A link in the row's actions | An item in the card's action sheet |
| `panel` | `exception.detail.panel`, `customer.detail.panel`, `dashboard.panel` | A card on the page | A collapsible section below the decision bar |
| `page` | `nav.page` | A menu entry and its own page | A drawer entry |
| `tile` | `settings.tile` | A control-panel tile | A tile |

**Contract.**
- A slot returns `SlotContent(title, html, actions)` or None. `html` is rendered from the
  plugin's own Jinja templates through OpenClear's environment (autoescape on), then
  sanitised (nh3, which is already a dependency) to an allow-list of tags and attributes.
- No inline scripts or styles: the CSP stays as it is. A plugin's JS and CSS ship as its own
  static files under `/static/plugins/<name>/`, loaded only on pages where its slot renders.
- Every slot respects permissions and the bank's choice: only for banks that enabled the plugin
  (`tenant_extension`), and a slot declares the permission it needs.
- A slot that raises or takes over 200ms to render is skipped and logged
  (`plugin.slot.failed`); the page always renders.
- Customer-scoped and account-limited sessions see only what their scope allows: the context a
  slot gets carries the request's `TenantContext`, and the plugin reads data through the same
  services (so `db/account_scope.py` applies).
- Actions a slot adds post to the plugin's own routes (`/ui/plugins/<name>/...`), with CSRF, and
  are audited like any other action.

**Example plugin.** `examples/plugins/openclear_example_panel`: fills one slot of each kind
(a "vendor score" panel on the exception page, a row action, a toolbar export, a page, a tile),
with its own static JS, and is used by the tests.

## 4. Phases (each one PR, three full suites before merging)

| Phase | What | How desktop is protected |
|---|---|---|
| **R0. Measure** | Playwright at 360, 768, 1024, 1280px over the key pages (dashboard, exception queue and item, issued/paid/ACH lists, action log, Users, People, Sign-on, control panel, uploads): no horizontal scroll, targets ≥ 44px, decision buttons visible without scrolling, axe clean. A baseline report of what fails. No UI changes. | 1280px screenshots become the reference |
| **R1. Foundations** | Breakpoint tokens; `responsive_table`; `nav_drawer`; `bottom_sheet`; lists moved one per commit. | The 1280px screenshot diff must be empty |
| **R2. Deciding** | Tap-to-Pay with Undo; Return reason sheet; score in the decision bar; collapsible action log; landscape-tablet two-pane queue. | Existing M3 tests + R0 checks |
| **R3. Everything else** | Dashboard, control panel sheets, People, Sign-on, uploads, the bank's admin pages. | Same |
| **R4. Customer API clients** | Backend (switch, permission, scoping, events), then the screens. | Security tests in the style of test_customer_people.py |
| **R5. Plugin UI slots** | Registry, `slot()` in templates, sanitising, static files, failure isolation, the example plugin. | A failing or slow slot never breaks a page (tested) |
| **R6. Bank approval** | Settings, pending memberships, approval queue, notices. | Off by default; existing invitation and SSO tests unchanged |
| mTLS | Nothing built (MB-D2). | — |

Release after R3 (phones and tablets), and after R6 (the rest), or together if they land close.

### R0 baseline (2026-10-13)

`tests/test_web/test_viewports.py` over 14 pages (the bank admin's 12, and the customer
admin's People and control panel) at 360, 768, 1024 and 1280px; details in
`tests/fixtures/viewport/REPORT.md`, the list the test holds the line on in `baseline.json`.

- **Already fine at every width:** no page scrolls sideways (wide tables scroll inside
  `.table-scroll`), axe is clean, and Pay and Return are on screen without scrolling on an
  exception (M3's sticky decision card), even at 360×780.
- **1024 and 1280px:** nothing fails. The 1280px screenshots are the desktop reference
  (`tests/fixtures/viewport/darwin/`, compared on macOS with SQLite).
- **What fails is touch targets** (4–17 kinds per page at 360px, 15–38 at 768px):
  - 768px is worse than 360px because the tablet still gets the desktop sidebar: every nav
    link is 30px tall (about 20 per page). R1's `nav_drawer` below 1024px fixes these.
  - On every page at 360px: the menu button (31px), Help (32px), the theme select (29px).
  - Lists: sort headers (`sort-button`, `sort-link`), the table filter, row checkboxes,
    and toolbar buttons (`a.button`, 35px), the "More filters" summary (18px): R1's
    `responsive_table`, filters in a `bottom_sheet`, and a 44px button size below 1024px.
  - The exception page: "Back to exceptions", "Add a note" and "More about this exception"
    (18px). The decision buttons themselves are 40–41px at 768px: R2.
  - Users: each row's Deactivate, Sign out everywhere and Reset password (link-style
    buttons, 16–32px): R1's actions column (a ⋯ sheet when more than two).
- **Noticed, not a layout problem:** lists sorted by when rows were made (issued items,
  paid items, ACH transactions, Users) have no tie-breaker, so rows made in the same second
  come in a different order on SQLite and Postgres (and could move between pages). Fixed in
  R1: every list sort ends with the id.

## Testing

- R0's viewport checks run in the suite from then on, so every later phase is measured.
- Desktop: screenshot comparison at 1280px for the converted pages.
- One manual pass on a real iPhone (Safari) and Android phone (Chrome), and an iPad in both
  orientations, after R2 and after R3; the embedded frame (online banking) on a phone.
- Plugin slots: the example plugin, a slot that raises, a slot that's slow, a slot returning
  a script tag (stripped), a customer-scoped session.

## Progress log

(Newest last.)
- 2026-10-13 Plan written and decisions MB-D1–D3 made with the product owner. Nothing built yet.
  Next: R0.
- 2026-10-13 R0 done: `tests/test_web/test_viewports.py` measures 14 pages at four widths
  (sideways scroll, 44px targets below 1024px, Pay/Return above the fold, axe) against
  `tests/fixtures/viewport/baseline.json`, and compares 1280px screenshots with
  `tests/fixtures/viewport/darwin/`. Results in "R0 baseline" above: only touch targets fail,
  at 360 and 768px. No UI changes. Next: R1.
- 2026-10-13 R1 done (branch `mobile-r1`, a commit per piece, the 1280px screenshots
  unchanged after each):
  - Breakpoints defined once (`--bp-phone-max` / `--bp-tablet-max` in app.css,
    `static/js/breakpoints.js`); the old 640/720px queries moved onto them;
    `tests/test_web/test_mobile_components.py` refuses any other width. Below 1024px: 44px targets everywhere, and the theme/Help controls get a
    strip of their own.
  - `nav_drawer` lives in base.html (one place already, so not a macro): the old phone
    dropdown is now an off-canvas drawer below 1024px; the page is `inert` while it's open.
  - `bottom_sheet` (`_macros/bottom_sheet.html`): content in place on wider screens (a
    `display: contents` wrapper, so the layout is the same), moved into a `<dialog>` below
    its breakpoint by JS. Used for the lists' More filters and row action menus.
  - `responsive_table` (`_macros/responsive_table.html`), with roles as planned; on a phone
    the sortable headers stay as a row of buttons above the cards. Moved, one per commit:
    issued items, paid items, stop payments, ACH transactions, ACH authorizations, accounts,
    customers, the action log, Users (⋯ sheet), People and invitations, API clients (⋯ sheet).
    There is no bulk uploads list page (an upload is reached from its list's upload form);
    the exception queue moves in R2.
  - Every list sort ends with the id (`web/list_search.py` `tie_breaker`, Users).
  - The viewport test covers 19 pages now; its seed gives rows distinct recent times, so the
    reference pictures are stable. Remaining baseline: the exception queue's checkboxes (R2)
    and the upload page's back link (R3).
  - Not done, deliberately: tablet rows folding the remaining columns into an expandable
    row. `hide-tablet` drops a column below 1024px instead (the item's page has it); no list
    needed either yet, since they fit at 768px. Revisit in R3 if one doesn't.
  Next: R2.
- 2026-10-13 R2 done (branch `mobile-r2`, a commit per piece, the 1280px screenshots
  unchanged after each):
  - The exception queue on `responsive_table` (new: `row_attrs` for the J/K keys, `header`
    for a select-all box, `th_class`; `th` is no longer escaped, which had broken the
    customers list's `scope="col"`). Checkboxes are a 44px label down each card's left edge;
    Network and Status in a bottom sheet; the bulk bar's Return reasons in a sheet. The
    bulk bar never stuck to the bottom of the screen: `.card`'s `overflow-x: auto` made the
    card its scroll container. The queue's card is exempted (its table scrolls in
    `.table-scroll`).
  - The decision card below 1024px is a compact bar: the fraud risk, Pay and Return in one
    row, a note below; the heading only for screen readers; Return's reasons in a sheet
    (R opens it). With two cards (revising and approving), only the last sticks. The
    confirmation dialog fits a phone.
  - On a phone the check image comes first, with Front/Back buttons. The exception's own
    action log entries, collapsed with the newest showing, for `audit_log:read` and never
    in a customer's session. New index `ix_audit_log_entry_tenant_resource`
    (tenant_id, resource_id), which SoD's "who presented it" and a customer's activity page
    also query by.
  - Undo: after a fresh recommendation the toast offers Undo (`?undo=<form id>`, only a
    form marked `data-undo` on the page), which withdraws it; *Withdraw my recommendation*
    on the page does the same with no time limit. A revision gets no Undo (it would withdraw
    the earlier one too). Audited as `exception.recommendation_withdrawn`; not a security
    event.
  - Two panes: decided to key them on a touch screen held sideways
    (`OpenClear.twoPane()`: `(pointer: coarse) and (orientation: landscape)`), not on
    width, since landscape iPads are 1024px and wider and a desktop must not change. The
    pane loads the item page's `[data-item-view]`; its forms are sent with fetch, the
    item's row refreshed from the list afterwards; Back and reloading work; turned upright
    the item becomes its own page. app.js's per-element setup now runs on a root
    (`enhance(root)`), so content put in later gets confirmations, busy buttons, toasts,
    local times, sheets. Inside the two panes targets are 44px whatever the width.
  - Not done: a ≥1024px touch screen outside the two panes still gets desktop-sized
    targets (no width query can tell it apart); `@media (pointer: coarse)` rules would.
    Revisit in R3. The recommendation's "awaiting approval" notification isn't taken back
    on Undo; the approver finds the item open again.
  Next: R3.
- 2026-10-14 R3 done (branch `mobile-r3`, a commit per piece, the 1280px screenshots
  unchanged after each):
  - The viewport test measures 58 pages now: the bank's detail and admin pages, settings as
    pages, a setting opened in its window, and the customer's People and Sign-on pages. It
    also reports a table wider than its `.table-scroll` or card below desktop (the page
    passed, but columns were cut off: Retire on check return reasons was off the edge).
    The rate limiter is cleared between pages. The baseline is empty: no known problem on
    any page at any width.
  - On `responsive_table`: security groups, check and ACH return reasons, a customer's
    accounts, Controls, the integration checklist, plugins, segregation of duties. ML models'
    tables (one row has a colspan) carry the same classes by hand. New role `text` (a line
    with no label); `sheet: "tablet"` puts row actions in the ⋯ sheet below desktop (Users,
    API clients), and a responsive table's cells may break a long address below desktop, so
    the action log narrows at 768px. The tablet's expandable rows weren't needed.
  - Phone-only layouts: an upload form's column guide (`table.column-guide`, all eight), the
    exception's expected-vs-presented (each label with its mismatch badge, then the values),
    the fraud model comparison.
  - 44px targets: a link on a line of its own (`.link-line`, 23 templates), key-value table
    links, the settings window's Close, the colour picker. Checkbox labels below desktop are a
    block with the box beside the first line (a flex label split the text around `<code>`).
    The notification preferences' checkboxes had no labels (axe, every width): fixed.
  - `@media (pointer: coarse)` gets the touch-target rules too, so a landscape iPad outside
    the two panes has 44px targets; a mouse at desktop widths doesn't (tested at 1366px).
  - Dashboard: its figures two to a row on a phone. It has no "Decide now" banner or
    uploads/controls tiles to reorder (§2 assumed them); adding them would change the
    desktop, so not done. The settings windows were already full screen on a phone (M4);
    now tested.
  Next: the manual pass on real devices (iPhone Safari, Android Chrome, iPad both ways, the
  embedded frame), then the release for phones and tablets; R4 after.
- 2026-10-16 Device review (in progress; the product owner, in a desktop browser's device
  toolbar, on a local copy with the viewport test's data plus check images). Findings so far:
  - Bulk uploads and exports aren't for a phone: `.not-phone` (hidden below 640px) on the
    lists' Bulk upload and Export buttons, Users' and People's exports, Billing's reports and
    exports, an upload's downloads and Controls' evidence export. Tablets keep them.
  - A check with no image said so only inside the collapsed *More about this exception*: now
    under the comparison, with *Upload an image* (the check chosen) for those who can.
  - The viewport test's reference pictures now fix date fields too (Controls' evidence export
    defaults to today, so its picture changed every day).
