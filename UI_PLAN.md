# UI plan

An evaluation of OpenClear's interface as it stands after UX_PLAN.md's waves 1–4 and
MOBILE_PLAN.md, and the fixes it calls for. Written 2026-10-19.

**What was looked at:**
- Fresh screenshots of the demo bank (`scripts/generate_screenshots.py`'s data): as a bank
  admin on desktop in light and dark, and at phone width (390 px); and as a customer's user
  (added for this review; the demo has none) on desktop and phone.
- `static/css/app.css` and the templates.
- What the axe check (`tests/test_web/test_accessibility.py`) covers.
- The ui-ux-pro-max skill's guidance for an enterprise banking tool.

**What's already good, and stays:**
- A plain, functional style that suits the job: the skill's match for enterprise dashboards
  is "Minimalism & Swiss".
- Pay and Return with icon and text, never colour alone. Mismatches marked "Doesn't match" as
  well as tinted.
- Lists that search on the server. Cards on phones. A skip link.
- Reduced-motion support. Native `<dialog>`s. A control panel with each setting's state.
- An axe check whose baseline is zero.

The problems below are mostly gaps between those parts, not a missing design.

## Findings

Severity follows the skill's priorities: accessibility and touch are critical, then layout and
navigation, then type, colour and forms.

| # | Finding | Evidence | Severity |
|---|---|---|---|
| F1 | **In dark mode the bank's accent colour is used as text on the dark background**: the bank name, the active navigation item and hover states. | Demo accent `#0f5b8a` on `#0f172a` is **2.45:1**; the default `#2563eb` is 3.45:1. Both need 4.5:1. (`app.css` `.sidenav a.active`, `.brand`) | Critical |
| F2 | ~~Any accent colour a bank picks is accepted unchecked.~~ **Wrong (corrected 2026-10-19, building UI1):** `tenant_service._validate_accent_color` already refuses an accent under 4.5:1 against white, so white button text is always readable. What's left is F1, plus a borderline accent as text on the light grey of the selected navigation item. | `tenant_service.py` | — |
| F3 | **The axe check only sees one case:** light mode, desktop width, a bank admin, the default accent. It never sees dark mode, a phone, a customer's session or a bank's own colour. That's why F1 went unnoticed. | `test_accessibility.py`: no theme, viewport or accent parameter; 19 pages, all as the bank. | High |
| F4 | **Headings are upside down.** `h1` is set to 1.4rem, but `h2` has no size, so it renders at the browser's 1.5em. Section headings come out bigger than the page title. | On the exception page, "What was expected vs. what was presented" is larger than "Check #10025". | High |
| F5 | **There are two link styles.** No rule sets link colour, so links in tables, "Back to exceptions" and "Upload an image" are the browser's own blue (and purple once visited). Other links ("Review queue →", "More filters") use the bank's accent. | `app.css` has no `a {}` rule. Visible on every list and detail page. | Medium |
| F6 | **The grey secondary button looks disabled.** It's solid `#6b7280` with white text: Export CSV, Show, More filters, and the filter summary on phones. Real disabled controls have no style of their own. | Screenshots: exceptions, customers, phone exceptions. | Medium |
| F7 | **The customer's dashboard shows six zeros when nothing needs deciding.** The customer's other main job, sending the issued-checks file, has no button there, and nothing says "all caught up". "Nothing due soon" is styled as a link. | Customer dashboard, desktop and phone. | High |
| F8 | **Who you are is shown oddly.** "Logged in as **Admin** (Maple Street Dental)" names the security group, not the person. The customer scope is shown in warning colours, so it looks like an alert. | `dashboard.html:23`; the `Scoped to` badge. | Medium |
| F9 | **The navigation is long and mixes kinds of link.** The bank side has 24 links. The last group mixes documentation with account actions. "Switch organization" shows even for someone with one organization. "Log out" is the only item without an icon. | `base.html:100–112`. | Medium |
| F10 | **The page controls don't sit in the layout.** The theme selector and Help float over the content card's top-right corner. On a phone they take a row of their own above the page title. | Dashboard, exceptions, customers; phone screenshots. | Medium |
| F11 | **The upload page starts with reference material.** A six-row table of column names in `snake_case` comes before the file field. The file field is the browser's unstyled "Choose File", and its label sits right under the table. | Customer upload, desktop. | Medium |
| F12 | **Number fields bring up the full keyboard on phones.** Amount and check-number fields have no `inputmode`: issued item, stop payment, paid item, ACH transaction, fraud training. | Only 6 `inputmode` attributes across 196 templates. | Medium (touch) |
| F13 | **Sign-in leaves out what password managers and people need.** The fields have no `autocomplete` values. "Organization slug" is jargon to an AP clerk. Nothing says what to do about a forgotten password; there's deliberately no self-service reset, and only the docs say to ask an administrator. | `auth/login.html`. | Medium |
| F14 | **The exceptions list on a phone.** The filter summary is a grey (disabled-looking) button, sorting is a lone "Status ↕" chip, and the cards leave out the date. On desktop, Network and Status sit at opposite edges, and "Page 1 of 1" doesn't line up with Prev and Next. | Exceptions, desktop and phone. | Low |
| F15 | **The bank's dashboard puts everything in one card.** It has a "Logged in as" line, and a tile that shows text ("Under 1 hour") where the others show numbers. It counts what's waiting but doesn't show any of it. | Bank dashboard. | Low |
| F16 | **The footer's licence notice takes five lines on a phone, on every page.** | Every page. | Low |
| F17 | **Smaller inconsistencies:** settings states written differently ("Warn" and "18 WARN"); some tiles have no state; the exception page's comparison table has an empty fourth column; a handful of raw hex colours in `app.css` rules (most of the 52 are the token definitions themselves) and 35 inline `style=` attributes in templates. | Settings panel; exception detail; `app.css`. | Low |
| F18 | **Still to be done by a person (from UX_PLAN.md):** the screen-reader and keyboard-only pass, and the user-testing sessions. Neither has happened. | UX_PLAN.md progress log, 2026-10-10 and 2026-10-11. | — |

## User flows

Six journeys were walked in a real browser, each step screenshotted. Three were as a customer's
user (three open items seeded): deciding, uploading an issued-checks file with a bad row, and
adding a stop payment. Two were as a bank admin: onboarding a new customer, then watching the
queue and the customer hub. Deciding was also walked at phone width. Where a flow goes after
each action was checked in the routes.

**What works:**
- A customer with items to decide lands on them.
- A bad upload row is named with the field at fault, and "Fix failed rows" opens a form with
  the error beside the field.
- Bulk Pay and Return confirm with the count and total.

**Where the flow breaks:**

| # | Journey | Finding | Evidence | Severity |
|---|---|---|---|---|
| FL1 | Customer decides | **Nothing leads to the next item.** After Pay you stay on the item you just decided, showing a toast. With three to decide, each one takes Pay → Back to exceptions → pick the next one. The item opened with the list it came from (`?from=`), but nothing uses that list. | `exceptions.py` `decide()` redirects to the same item; walk steps A-02 to A-04. | High |
| FL2 | Every page while items are open | **The banner's "Decide now" link is hidden** under the floating theme selector and Help button (F10). Only a sliver of it shows. It's the link that page exists for. | Walk: item page, upload page, stop-payment page; `base.html:134`. | High |
| FL3 | Customer decides | **Pay is one click and can't be undone,** even for a check that's not in the issued file and carries a medium fraud-risk flag. Return asks for a reason; Pay asks nothing. Only high risk asks for confirmation. **Decided by the product owner (2026-10-19):** a decision can be changed until the cut-off (UI-D13). | Walk step A-03. | High |
| FL4 | Customer, any time | **The dashboard can only be reached by clicking the bank's name.** The navigation has no Dashboard or Home item, and a customer with open items is always sent to the list. | `base.html:50` (`/ui/?home=1` on the brand); `dashboard.py`. | Medium |
| FL5 | Customer uploads | **The upload result starts with the file's metadata.** Kind `issued_items`, the size in bytes and the SHA-256 come before the outcome. The outcome is an amber "1 of 2 succeeded" badge. "Records this upload created" lists "row 2 · issued_item · active", without the check number or amount. "Back out this upload", which undoes the whole file, sits under "Back to issued items" in the same grey as the other buttons. | Walk step B-03. | Medium |
| FL6 | Customer adds a stop payment | **"Effective date" is required and starts empty.** Pressing Create only opens the browser's date picker. A stop payment is almost always effective today. | Walk step C-03; `stop_payments/form.html:22`. | Medium |
| FL7 | Bank onboards a customer | **The customer's checklist sends you somewhere and doesn't bring you back.**<br>• "Add an account" and "Add a user" open the generic `/ui/accounts/new` and `/ui/users/new`. Nothing is preselected, so you pick the customer again.<br>• Saving lands on the Accounts or Users list, not the checklist. You find your own way back, every step. | `wizard_service.CUSTOMER_STEPS`; `accounts.py:112`; walk step D-04. | High |
| FL8 | Bank onboards a customer | **"Add this customer's users" has the bank set the customer employee's password** (`/ui/users/new` takes one). Customers already have an emailed invitation, with a link to choose their own password (People). The checklist uses the first and not the second. | `wizard_service.py:170`; `people.py` `accept`. | Medium |
| FL9 | Bank onboards a customer | **The checklist is hard to read.** Done and not done are tiny coloured dots, the optional steps' button reads "— mark done", and the "Back to customer" button is the disabled-looking grey (F6). | Walk step D-04; `wizard/customer.html:48`. | Low |

## Decisions (recommended)

- **UI-D1. Polish, not a redesign.**
  - The look stays plain and functional, with the system font stack.
  - The skill suggests IBM Plex Sans and a gold-and-purple dark palette. I've set both aside:
    - Every bank brings its own accent colour, so OpenClear can't have a palette of its own.
    - A web font is another download, and pages embedded in a bank's online banking should
      stay light.
  - What's taken from the skill: one heading scale, semantic colour tokens, 4.5:1 contrast in
    both themes, visible focus, empty states with a next step, and 44 px touch targets (already
    met on phones and tablets).
- **UI-D2. The bank's colour fills; a readable shade of it is the text.**
  - Banks keep their brand colour. The existing rule stays: an accent too light for white
    button text is refused.
  - From that colour, `colors.accent_text` works out the shade to use as text, links and focus
    rings in each theme: the least change toward black (light theme) or white (dark theme)
    that reaches 4.5:1 on both of that theme's backgrounds.
  - Fills (`--accent`, with `--on-accent` text) use the colour itself; text uses
    `--accent-text`.
  - The Branding setting shows a preview of both themes, and says when the shade was adjusted
    from the bank's colour.
  - It's a small pure function in Python with tests; no JavaScript, and nothing stored.
- **UI-D3. One link style.**
  - `a { color: var(--link) }`, where `--link` is the accent's text colour for that theme.
  - Underlined in running text and tables, so colour is never the only sign.
  - No separate colour for visited links: in a work tool, "visited" carries no meaning.
- **UI-D4. One heading scale, set once:** `h1` 1.5rem, `h2` 1.2rem, `h3` 1rem. Headings inside
  a card are `h2`. Each page's own heading rules (`.panel-section h2` and so on) go, except where
  a dialog needs smaller.
- **UI-D5. Secondary buttons are outlined, and disabled has its own look.**
  - Secondary: a border and text in `--link`, on the page background.
  - Disabled: reduced opacity, a not-allowed cursor, and `aria-disabled` or `disabled`.
- **UI-D6. An account menu.**
  - It sits at the foot of the navigation, under the person's name: Security, the theme,
    Switch organization (only with more than one), documentation, and Log out.
  - Help stays in the page's header row, beside the title, not floating over the card.
  - The dashboard greets the person by name. The customer scope is a neutral label, not a
    warning colour.
- **UI-D7. The axe check covers what people actually see.**
  - Light and dark.
  - 1280 px and 390 px.
  - A bank admin and a customer's user.
  - A dark and a light brand accent.
  - It runs on the same pages, so a contrast fix can't quietly regress in the theme nobody
    looked at.
- **UI-D8. A customer's dashboard leads with their two jobs:**
  - "Decide N items, due …", or "Nothing to decide. You're all caught up".
  - "Upload issued checks", with the last upload's date and result.
  - The counts stay below.
- **UI-D9. The footer gets shorter, but still meets the AGPL.**
  - One line: copyright, "free software under the AGPL, with no warranty", and links to the
    licence and the source.
  - The full notice moves to the licence page, which every page links to.
  - The interface must still show AGPL §5(d)'s "Appropriate Legal Notices". The product owner
    confirms the wording before it ships.
- **UI-D11. After a decision, the next item.**
  - Deciding (or recommending) moves on to the next item still open in the list it was opened
    from (`from=`, filters kept).
  - The toast names what was just done: "Check 7003 paid".
  - With nothing left, it goes to the list, which says so.
  - It works the same in the embedded frame and on a phone. The decided item stays reachable
    from the list and the action log.
- **UI-D12. A checklist step brings you back.**
  - Each customer step links with `customer_id=` and `return=` (a local path, checked like
    `next`).
  - The form preselects the customer, and saving returns to the checklist with the step ticked.
  - Adding the customer's people sends the emailed invitation, so the bank never chooses an
    employee's password.
  - Setting a password for someone stays available on the bank's Users page, for the cases that
    need it.
- **UI-D13. A decision can be changed until the cut-off.** *(The product owner's decision,
  2026-10-19.)* Positive pay services usually work this way. It replaces "a decision can't be
  changed afterwards". It means no confirmation on Pay is needed: a mistake can be put right
  until the cut-off.
  - **The cut-off is a time of day, set for the whole bank, with exceptions per customer.**
    *(The product owner's rule, 2026-10-19: customers negotiate their own cut-off times.)*
    - **The bank's cut-off:**
      - a time of day in the bank's time zone, both new settings;
      - one time per network, because the default decisions already work per network and
        banks often run ACH returns on a different schedule (the ACH time starts as the check
        time);
      - a control-panel tile.
    - **A customer's cut-off:** a different time for one customer, per network, agreed with
      the bank.
      - It's stored on that customer's disposition setting, beside the default decision it
        already holds.
      - Bank staff with `tenant:manage` set it, on the customer's page.
      - The customer sees theirs ("Your cut-off, agreed with your bank: 14:00 ET") but can't
        change it.
    - **Who can change a cut-off:** a change at either level is recorded in the action log.
      It's also a protected setting under the bank's second-person rule (dual control for
      settings), since it moves when decisions become final.
    - **Each item's deadline is set from it when the item arrives.**
      - It's the first cut-off (the customer's if they have one, otherwise the bank's) that's
        at least a minimum review time after the item arrives. That minimum is a bank
        setting, 2 hours to start, so an item arriving at 10:55 isn't due at 11:00.
      - The cut-off falls on a business day: Monday to Friday, skipping the Federal Reserve's
        holidays (worked out from their fixed rules in code, with a test per year), and any
        closures the bank adds.
      - It's stored in `decision_deadline`, as now, in UTC; time zones and daylight saving go
        through `zoneinfo`.
    - **Changing a cut-off affects only items that arrive afterwards.** Open items keep the
      deadline they were shown, because the customer has already been told it.
    - **Every item gets a cut-off once the bank sets its own.** That includes:
      - the bank's own items, which have no customer;
      - customers without a default decision. For them, the cut-off locks decisions already
        made; undecided items stay open and show as overdue; a decision made after the
        cut-off is final at once.
    - **It replaces the response window.**
      - The default decision still says what happens to an unanswered item at the cut-off;
        only "decide within N hours" goes.
      - Until a bank sets its cut-off, nothing changes on upgrade:
        - customers with a response window keep getting deadlines from it;
        - items without a deadline are final when decided, as today.
      - Once the cut-off is set, the response window is no longer used, and its field is
        hidden.
    - **Where it shows.**
      - The customer hub gains a Cut-off column, with negotiated times marked.
      - The customer's exceptions and dashboard say "Decide by 11:00 ET" (in the viewer's own
        time, as other times already are).
      - The evidence export lists each customer's negotiated cut-off and when it was agreed.
  - **Before the cut-off, a decision is recorded but not yet sent.**
    - The item shows as decided, for example "Paid · you can change this until 11:00 (in 2 h)",
      with a **Change decision** button.
    - Nothing leaves OpenClear yet:
      - no decision-feed sequence number;
      - no `exception.decided` webhook;
      - no line in a decision file;
      - an ACH return isn't marked settled.
    - That's what makes changing it safe: the bank's core never saw the first answer.
  - **At the cut-off, the decision locks and goes out.**
    - The deadline sweep already decides items nobody answered. It also locks the ones that
      were answered. Locking assigns the feed sequence, emits `exception.decided`, puts the
      decision in the next decision file, and marks an ACH return settled, in one transaction
      per item.
    - From then on it's final. The page says "Final since 11:00".
    - A decision made after an item's cut-off is final at once.
  - **Changing follows the same rules as deciding.**
    - Return still needs a reason from the catalogue.
    - Segregation of duties is checked again.
    - Under dual control:
      - a change is a new recommendation;
      - the decision already in place stands until someone else approves the change, so an
        item can't drop back to undecided just before the cut-off and fall to the default;
      - a change nobody approves by the cut-off leaves the earlier decision to lock.
    - Deciding several items at once can change several at once, by the same rules.
  - **Every decision is kept.**
    - Each one is its own `decision` row. A change marks the earlier row superseded (by whom,
      when); it's never edited or deleted.
    - The action log records each one.
    - The page shows the history: "Returned by Dana 09:12 · changed to Pay by Dana 09:40".
    - The fraud model learns only from the locked decision.
  - **The API says it too.**
    - A decision's answer gains `locked_at` (null until the cut-off) and `changeable_until`.
    - `PUT /api/v1/exceptions/{id}/decision` changes it before the cut-off. Afterwards it
      answers 409 with the time it locked.
    - The decision feed and `exception.decided` carry locked decisions only. That's a change
      of timing for integrations: an integration that took `exception.decided` as "decided
      just now" gets it at the cut-off instead. It goes in the CHANGELOG, the API docs and the
      SDKs.
  - **Counts and reminders.**
    - A decided-but-changeable item no longer counts as "to decide": not in the banner, the
      reminders, or the Exceptions badge.
    - It has its own filter, "Decided, changeable".
    - The customer's dashboard (UI-D8) says how many decisions are still open to change, and
      until when.
  - **The toast after a decision (UI-D11)** offers **Undo** while the decision can be changed.
    Undo takes the item back to undecided, recorded like any change. It's offered only without
    dual control; under dual control a change goes through approval.
- **UI-D10. Some things wait for a person.** The screen-reader and keyboard pass, and user
  testing (F18), stay on MASTER_PLAN.md's waiting list. This plan doesn't replace them. Its
  stages make the screens easier to test once those sessions happen.

## Stages

Each stage is one pull request. Before merging: the three full suites, the security gates, and
the screenshot set taken again for a before-and-after comparison. The screenshot script gains
the customer's user, dark mode and phone width that this review added by hand.

| Stage | What | Closes |
|---|---|---|
| **UI1. Contrast and the colour tokens** | • The accent's derived tokens (UI-D2), with the Branding preview and the adjusted-for-legibility note.<br>• `--link` and the one link style (UI-D3).<br>• Semantic tokens for success, danger, secondary, and Pay and Return, replacing raw hex in `app.css`.<br>• The extended axe check (UI-D7), which must pass in both themes. | F1, F2, F3, F5, part of F17 |
| **UI2. The journeys** | • The next item after a decision (UI-D11).<br>• The page header row, with title, actions and Help together. This frees the banner's "Decide now" link.<br>• A Home item in the navigation.<br>• The checklist that brings you back, and invitations for a customer's people (UI-D12); the checklist's own look.<br>• The stop payment's effective date defaulting to today.<br>• The upload result: the outcome first, "Fix failed rows" as the main action, check number and amount on created records, the file's metadata under "File details", and "Back out" styled as the destructive action it is.<br>• A browser test that walks each journey, as this review did. | FL1, FL2, FL4–FL9, F10 |
| **UI3. Changeable until the cut-off** | • Each decision stays changeable until its item's cut-off, then locks (UI-D13):<br>&nbsp;&nbsp;– `decision` gains `locked_at`, `superseded_at` and `superseded_by_user_id` (one migration; the table's RLS policy already covers new columns);<br>&nbsp;&nbsp;– the deadline sweep locks;<br>&nbsp;&nbsp;– the feed sequence, webhook, decision file and ACH settlement move to the lock.<br>• The cut-offs:<br>&nbsp;&nbsp;– the bank's time zone, cut-off per network and minimum review time (a control-panel tile);<br>&nbsp;&nbsp;– a customer's negotiated cut-off per network (on its disposition setting, from the customer's page; read-only to the customer);<br>&nbsp;&nbsp;– both protected settings under the second-person rule;<br>&nbsp;&nbsp;– the bank's closures (a new table, with its RLS policy);<br>&nbsp;&nbsp;– the business-day calendar with the Federal Reserve holidays;<br>&nbsp;&nbsp;– deadlines set from the cut-off on arrival, falling back to the response window until the bank sets its own;<br>&nbsp;&nbsp;– the Cut-off column in the customer hub;<br>&nbsp;&nbsp;– the evidence export.<br>• Change decision on the page, Undo in the toast, the history on the item, "Decided, changeable" in the list.<br>• The API (`locked_at`, `changeable_until`, `PUT …/decision`), with `scripts/export_openapi.py`, the SDKs and the contract test.<br>• Data dictionary, CHANGELOG (the webhook and feed timing), the end-user and admin docs ("A decision can be changed until the cut-off").<br>• Tests:<br>&nbsp;&nbsp;– a change before the cut-off;<br>&nbsp;&nbsp;– refused after it;<br>&nbsp;&nbsp;– under dual control, the earlier decision stands until the change is approved;<br>&nbsp;&nbsp;– nothing reaches the feed, webhooks or files before the lock;<br>&nbsp;&nbsp;– a bank without a cut-off sees no change in behaviour;<br>&nbsp;&nbsp;– a customer's cut-off wins over the bank's;<br>&nbsp;&nbsp;– the minimum review time pushes a late item to the next business day;<br>&nbsp;&nbsp;– weekends, holidays and closures are skipped;<br>&nbsp;&nbsp;– a daylight-saving change keeps the local time;<br>&nbsp;&nbsp;– changing a cut-off leaves open items' deadlines alone. | FL3 |
| **UI4. Type and components** | • The heading scale (UI-D4).<br>• Outlined secondary buttons and the disabled style (UI-D5).<br>• The pager lined up.<br>• The comparison table without its empty column.<br>• Inline `style=` widths replaced by a few width classes. | F4, F6, F14 (desktop), F17 |
| **UI5. The customer's dashboard and navigation** | • The dashboard by jobs (UI-D8).<br>• The account menu, greeting and neutral scope label (UI-D6).<br>• The upload page:<br>&nbsp;&nbsp;– the file field first, styled as the drop zone it already is;<br>&nbsp;&nbsp;– the column reference under a "File format" disclosure, with plain names beside the column names.<br>• The phone exceptions list: the filter summary as a plain text row that opens the sheet, sorting in the same sheet, and the date and deadline on each card. | F7, F8, F9, F11, F14 (phone) |
| **UI6. Forms** | • `inputmode="decimal"` on amounts and `inputmode="numeric"` on check and account numbers, everywhere.<br>• Sign-in: `autocomplete` values; "Organization" with a hint ("the short name in your sign-in link") instead of "slug"; and "Forgot your password? Ask your administrator to reset it", per the current policy.<br>• A test that fails on a new amount or check-number field without `inputmode`. | F12, F13 |
| **UI7. The bank's dashboard and the rest** | • The bank's dashboard: the tiles, then the five items due soonest, with their customers; no "Logged in as".<br>• The shorter footer (UI-D9, after the product owner's check).<br>• Settings states written one way, and a state on every tile that has one. | F15, F16, F17 |

**Order:**
1. UI1 first. It fixes the only WCAG failures found (F1, F2), and its stronger axe check then
   watches every later stage.
2. UI2 next, because it fixes what slows people down most, every day: one item at a time, a
   hidden link, a checklist that loses you.
3. UI3 after it. It changes what a decision is, so it touches the decision service, the
   sweep, the API and integrations as well as the screens. Its own pull request keeps that
   reviewable. UI2's next-item flow and toast come first, and UI3 adds Undo to them.
4. UI4 before UI5–UI7, which build on its components.

**What's not here:** a new visual identity, charts, animation, or a framework or CSS library.
None of the findings calls for them, and each would mean a front-end build step this project
doesn't have.

## Progress log

- 2026-10-19: Plan written from the evaluation described at the top. The review's screenshots
  (bank admin light, dark and phone; customer's user desktop and phone) were taken into a
  scratch folder, not `docs/screenshots`. The skill's colour palette and landing-page pattern
  were set aside (UI-D1); its style, typography-hierarchy, contrast, empty-state and touch
  guidance were used.
- 2026-10-19: User flows added (FL1–FL9, UI-D11 to UI-D13), from six journeys walked in a
  browser as a customer's user and as a bank admin. A new stage UI2 (the journeys) comes second;
  the later stages are renumbered UI3–UI6.
- 2026-10-19: The product owner decided decisions can be changed until the cut-off (FL3).
  Written up as UI-D13 and stage UI3, in place of the Pay confirmation. The cut-off is each
  item's decision deadline. For items without one, a new bank setting is recommended: a decision
  cut-off time, with the bank's time zone. Without it, decisions stay final when made. Later
  stages are renumbered UI4–UI7.
- 2026-10-19: The product owner set the cut-off rule: bank-wide, with exceptions per customer,
  so customers can negotiate their own. UI-D13's cut-off rewritten:
  - a time of day per network in the bank's time zone;
  - a customer's negotiated time on its disposition setting, set by bank staff;
  - both protected settings;
  - deadlines from the first cut-off after a minimum review time, on a business day;
  - the response window kept only until a bank sets its cut-off.
  Stage UI3 gains the settings, the calendar and their tests.
- 2026-10-19: UI1 built on `ui1-contrast` (stacked on `rf-review-fixes`):
  - `openclear/colors.py`, also used by the accent check, so the contrast maths has one home;
  - `accent_style` in both page heads; `--accent-text`, `--on-accent` and `--link`, with every
    text, outline and border use of the accent moved to the readable shade;
  - one link colour;
  - tokens for the secondary, Pay and Return fills;
  - the Branding preview;
  - the axe check in five views: desktop, dark with a dark accent, phone with a borderline
    accent, a customer's user, and customer dark on a phone. Run against the old CSS, the dark
    views fail on the bank name, the dashboard links and "More filters"; with UI1 every view
    passes.
  F2 turned out wrong (the accent's refusal rule already existed) and F17 overstated; both are
  corrected above. The desktop reference pictures were rewritten for the link colour.
- 2026-10-19: UI2 built on `ui2-journeys`:
  - **After a decision, the next item:** Pay or Return moves on to the oldest item still open
    in the queue it was opened from (its network and customer filters), with a toast naming
    what was done. With nothing left it goes to the list ("Nothing else is waiting for you
    here"). The tablet's two-pane queue does the same, and Back returns to the decided item.
  - **Recommendations stay on their item for now,** because the toast's Undo withdraws that
    item's recommendation. UI3's Undo will carry the item, and then they move on too.
  - **The page's top row:** the decide banner and Help in the flow of the page; the theme in
    the navigation (or the top row where there's no navigation). The floating controls, and
    the CSS that kept content clear of them, are gone.
  - **Home** at the top of the navigation.
  - **The customer's checklist** links with the customer chosen, and saving returns to it.
    Its states are in words ("Done", "To do", "Optional"), and its buttons read
    "Mark done" / "Mark not done".
  - **The stop payment's effective date** starts as today. The form keeps what was typed when
    it comes back with an error (it came back empty before).
  - **The upload result:** the outcome first ("1 issued check added. 1 row needs fixing."),
    "What this upload added" with each check's number, amount and payee, the file's details
    under a disclosure, and "Back out" in its own danger-styled section.
  - **`tests/test_web/test_journeys.py`** walks the journeys in a browser: deciding three in a
    row; "Decide now" not covered at 1280 and 390 px; Home; a stop payment from the check
    number alone; the checklist round trip; an upload with a bad row, fixed.
  **Not done, and why:** UI-D12's invitations for a customer's people from the bank side.
  Users the bank adds go through the bank's second-person rule for access changes
  (`access_changes.hold_new`). An invitation creates the membership only when it's accepted,
  so as things stand it would bypass that rule. It needs its own design: hold the invitation,
  or hold the membership on acceptance. It's left as a follow-up, and until then "Add a user"
  opens the bank's form with the customer chosen.
