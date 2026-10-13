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
show), where it's a recommendation awaiting approval it withdraws it (the existing path). Banks
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
