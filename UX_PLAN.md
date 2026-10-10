# User experience plan

This plan comes from a review of `positive_pay_ux_analysis_recommendations.md` (an outside
UX analysis) against what OpenClear actually does. The analysis was written about positive pay
products in general, so about half of it describes things OpenClear already has; those are
listed below so they aren't rebuilt. The rest, plus a few gaps the analysis missed, are the
phases here.

**Status: planned, not started.**

## Who uses OpenClear, and what matters most to each

| Who | What they're doing | What hurts most |
|---|---|---|
| **Customer users:** AP clerks, controllers and CFOs at the bank's business customers, usually inside the bank's online banking (embedded) and often on a phone | Sending issued checks; deciding exceptions before the cutoff | Getting an issue file accepted; noticing and deciding items in time |
| **Bank staff:** treasury support, operations, fraud officers, admins | Onboarding customers; watching exceptions across customers; helping customers | Customers' files in every layout; chasing customers before cutoffs |
| **Platform operator:** whoever runs an installation for several banks | Provisioning banks; watching jobs and health; support | No web view across banks; supporting a bank without seeing what it sees |

## Already in place (from the analysis, no work needed)

| The analysis asks for | OpenClear has |
|---|---|
| Handling a missed cutoff without staff stepping in | Per-customer automatic decisions at the deadline (pay or return), with a notice to the customer (`auto_disposition_service`) |
| Deadline visibility | "Decide by" in relative time on each exception and in the list. The dashboard leads with exceptions needing attention, those due within 24 hours or overdue, and the oldest waiting |
| Check image next to the item | The exception page shows the check's front and back, with the amount read from the image, beside the presented and issued details |
| Readable upload errors | Row-level results ("row 3: No account found with account number …"); a rejected file says why |
| Onboarding steps | The Getting Started checklists for a bank and for each customer (`wizard_service`) |
| White-labelling | Logo, favicon, accent colour and support contacts per bank |
| Clear role boundaries | Fine-grained permissions per bank, customer scoping, segregation of duties (1.9) |

## Where this plan departs from the analysis

- **No "Login As" impersonation for platform staff.** Letting a platform employee see a
  bank's customer data at will runs against segregation of duties and against what banks'
  third-party risk reviews ask. U8 builds **bank-granted support access** instead.
- **No BAI2 for issue files.** BAI2 reports balances and transactions; companies don't send
  issued checks in it. The layouts that matter are delimited files (CSV and similar),
  fixed-width ARP layouts, and Excel.
- **Added: accessibility** (WCAG 2.1 AA), which banks increasingly require of vendors and
  the analysis doesn't mention.
- **Added: decisions inside online banking.** Most customer users will reach OpenClear
  embedded in their bank's online banking, often in its mobile app. Mobile work (U6) is
  tested in that frame, not only as a standalone site.

## Phases

| Phase | What | Notes |
|---|---|---|
| U1 | **Issue-file layouts (mapping).**<br>• A customer (or bank staff for them) defines a **layout** for its issued-check files: delimited (any delimiter, header row or not, columns by name or position), fixed-width (start and length per field), or Excel (sheet and columns).<br>• Fields: account number, check number, amount, issue date, payee, and void flag. Amount formats: implied decimals, a separate cents column, thousands separators. Date formats are named (e.g. `MMDDYY`). Values can be trimmed and padded.<br>• **Building a layout from a sample file:** upload a sample, see its first rows, click columns or drag ranges to assign fields, and preview the parsed result before saving.<br>• A customer can have several layouts and a default. Uploads, the drop folder and the API use them. The layout is recorded with each upload.<br>• Bank staff get a library of the common ARP layouts as starting points. | Uses the import-format plugin point (PLUGIN_PLAN.md), so a layout is a configured format, not code. ACH authorizations and accounts can use it later. |
| U2 | **Fixing an upload in the browser.**<br>• The upload result lists failed rows with the reason beside **the field at fault** ("Amount: '1,2O0.00' contains a letter").<br>• Failed rows can be edited in place and resubmitted. Only those rows are imported, recorded as corrections to the original upload, with who fixed them.<br>• A failed upload offers "Save these settings as a layout" when the failure was a column or format mismatch (U1).<br>• A drag-and-drop zone on upload pages. | The original file stays as received (it's signed); fixes are separate, audited records. |
| U3 | **Reminders before the cutoff.**<br>• New notification: "N items to decide, due in X", sent to the people who can decide (or recommend) for that customer.<br>• Timing per bank (e.g. 2 hours and 30 minutes before), with a customer override. Grouped: one message per person per reminder, not one per item.<br>• Email and SMS through the existing notification channels and preferences. It also goes to plugin notification channels and the existing `exception.deadline_approaching` webhook.<br>• A final notice when items were decided automatically at the deadline (already sent; reviewed for clarity). | Respects each person's notification preferences and quiet hours if they set them. |
| U4 | **Faster decisions.**<br>• **Pay** and **Return** as two clear buttons (a green check, a red cross, with text, never colour alone), replacing the dropdown.<br>• A reason is asked for only on Return, and for checks it comes from a **catalogue of check return reasons**, as ACH already has: stop payment, altered, not authorized, duplicate, refer to maker, and the bank's own. Pay takes an optional note.<br>• **Several items at once:** select items in the list and Pay or Return them together, with one confirmation that shows the count and the total amount. Dual control, segregation of duties and the high-risk confirmation still apply to each item. It's offered only to someone who could decide each item one by one.<br>• **Keyboard:** next/previous item, P/R with a confirmation step. | Check return reasons get their own small table and admin page, like `ach_return_reason`. The API keeps accepting a free-text `reason_code` for compatibility. |
| U5 | **"Decide now" for customer users.**<br>• When a customer user signs in with items to decide, they land on those items, not the general dashboard.<br>• A **persistent banner** on every page while items are open: "4 items to decide, 1 h 15 m left" (or "due tomorrow 11:00"), with a link. It goes amber within 2 hours and red within 30 minutes, with text, not just colour. Embedded pages show it too.<br>• The exception page leads with what differs from the issued check, the image and the decision buttons. Other details collapse under "More". | The banner is server-rendered and refreshed by the page's existing timer, so it still works where scripts are restricted. |
| U6 | **Mobile and embedded.**<br>• An audit of every customer-facing page at phone widths (360–430 px) and inside the embedded frame: exceptions list and detail, approvals, issued items, uploads, stop payments.<br>• Fixes: the list becomes cards on narrow screens, decision buttons are thumb-sized and stay in reach at the bottom, images pinch-zoom, tables scroll inside themselves rather than the page.<br>• Tested on iOS Safari and Android Chrome, and in an iframe the way `@openclear/embed` places it. | The embed library already reports height changes to the host page; U6 checks that holds as cards expand. |
| U7 | **Bank staff: the customer hub.**<br>• The Customers list gains: items open, items due today, the nearest deadline, the last issued-file upload, and the customer's decision default at the deadline.<br>• It's sorted by "needs action" first, and filterable by "no issue file in N days" (a common sign of a customer who has stopped sending them).<br>• From a customer: send a reminder now (U3), open its uploads, its layouts (U1), its users. | |
| U8 | **Bank-granted support access** (instead of impersonation).<br>• A bank admin grants a named platform support person access for a stated reason and period (e.g. 4 hours), read-only by default. Write access needs a second bank admin's approval (as in C1).<br>• The support person works under their own name, with a banner saying whose access they're using and until when. They can't see secrets, change users, groups or settings, or decide items.<br>• Every grant, use and expiry goes into the bank's action log and the evidence export, and is visible to the bank under Settings → Support access.<br>• Platform staff can't grant themselves access. | Needed before an operator UI (U9) can link into a bank. |
| U9 | **Platform operator view.**<br>• A web page for the platform API key holder (or a platform admin role) showing every bank with its status, exceptions open and overdue, jobs queued and failed, the last import, the controls summary (C4), and the security-event delivery state (SIEM_PLAN S5).<br>• Provisioning a bank from the web, using the existing provisioning service and the bank's Getting Started checklist.<br>• Links into a bank only through U8. | Today these exist as platform APIs only. |
| U10 | **Accessibility (WCAG 2.1 AA).**<br>• An audit of the main journeys (sign in, dashboard, exceptions, decide, upload, settings) with automated checks (axe) and a manual pass with VoiceOver and NVDA and keyboard only.<br>• Fixes: labels, focus order and visible focus, colour contrast in both themes, announcements of changes (the banner, upload results), tables with headers, error messages tied to fields.<br>• A published accessibility statement, and an axe check in the test suite for the main pages so they stay compliant. | Runs alongside the other phases; each new page in the other phases is built to it. |
| U11 | **A control panel for settings.** Settings and Admin become one control panel. Today they're two long grids of form cards: Organization settings alone has twelve.<br>• **Tiles grouped into sections:** Organization; Security & access; Decisions & rules; Integrations; Data & compliance. A search box filters the tiles.<br>• **Each tile has an icon, a name and its current state**, e.g. "Dual control · On", "Segregation of duties · Warn", "SSO · 2 connections", "Controls · 1 FAIL". An admin sees how things stand without opening anything.<br>• Tiles appear only for what the person's permissions allow.<br>• **Small settings open in a modal:** branding, timeouts, dual control, contact info, messages, password policy, embedding origins, reminders (U3), the customer's decision default.<br>• **Lists and workflows open as full pages from their tile:** SSO connections, security groups, users, webhooks, API clients, decision files, the fraud model, Model risk, Controls, layouts (U1). A modal holding tables and sub-forms is worse than a page.<br>• **Every modal has its own URL** (e.g. `/ui/settings/branding`), so the Controls page, the Getting Started checklist and the docs can link straight to a setting. Without JavaScript the same URL is a plain page. It works the same in the embedded frame.<br>• **Modal behaviour:**<br>&nbsp;&nbsp;– native `<dialog>`, as the help and session dialogs already use;<br>&nbsp;&nbsp;– focus kept inside, Escape closes, focus returns to the tile;<br>&nbsp;&nbsp;– a warning before closing with unsaved changes;<br>&nbsp;&nbsp;– saving updates the tile's state in place, and errors show beside their fields;<br>&nbsp;&nbsp;– full-screen on phones.<br>• **The same pattern at three levels:**<br>&nbsp;&nbsp;– the platform operator (U9): banks, jobs, platform settings;<br>&nbsp;&nbsp;– the bank: everything above;<br>&nbsp;&nbsp;– each customer: its users, issue-file layouts, reminders, decision default, banner. | Mostly templates, CSS and a small script. The settings routes stay as they are, each answering as a full page or as a modal's content. Later phases (U1, U3, U8) add their settings as tiles rather than as more cards. |
| U12 | **Lists, navigation and shared components.**<br>• **Lists search the server.** The filter box and click-to-sort on every table only search and sort the rows on screen, while the lists are paginated on the server. So a check that's on another page looks as if it isn't there. Filtering and sorting move to the server across all rows.<br>• **Finding an item:** issued items, paid items, ACH and exceptions gain filters for check number, amount (exact or range), payee or originator, account, customer and date range. A **global search** in the header jumps to matching items by check number, trace number or amount.<br>• **Export** of the current filtered list as CSV, for issued items, paid items, ACH and exceptions. Users already has it.<br>• **Navigation by work, not data type:** Exceptions at the top, with a count badge (as Approvals has). One name for the action log, which is "Audit Log" in the nav but "action log" in the pages and docs.<br>• **Context:** page titles that name the item (e.g. "Check 1234 · $5,000.00 · Exception"). A way back to the list you came from, filters kept.<br>• **Empty states** that say what to do next, with the button for it.<br>• **Shared components**, built once and used by every later phase:<br>&nbsp;&nbsp;– a dialog (native `<dialog>`, as help and session already use);<br>&nbsp;&nbsp;– a confirmation for irreversible actions, replacing the browser's `confirm()`;<br>&nbsp;&nbsp;– a toast for a successful save;<br>&nbsp;&nbsp;– a busy state on submit that stops double submission. | Server-side list queries use the existing repositories and pagination, and work without JavaScript. |

## Waves

The phases are built in **waves grouped by screen area**, not one phase at a time. Several
phases change the same screens: U4, U5, U6 and U10 all change the exceptions list and
detail. Building them together means each journey is tested once, by hand, on desktop, a
phone, inside the embedded frame and with a screen reader. Shared pieces come first, so
later phases reuse them instead of each building and testing its own.

**Each wave is one merge to `main`:** one run of the three full suites (SQLite, Postgres,
Linux), and one manual pass over the screens it changed. Within a wave, commits run only the
tests they touch. That's five merges in all, instead of about a dozen. MASTER_PLAN.md places these waves among the WAF and SIEM waves.

| Wave | Phases | Contents | Manual pass |
|---|---|---|---|
| **0. Foundations** | U12; U10 (the baseline) | • Server-side list search, filters, sort and export; global search.<br>• The navigation and context changes.<br>• The shared dialog, confirmation, toast and busy-state components.<br>• **The axe accessibility check in the suite**, on the main pages, so every later wave is checked automatically.<br>• A baseline accessibility audit that records the fixes each later wave makes on its own screens. | The lists and navigation |
| **Before wave 1** | (user testing) | One round of sessions with real AP clerks and bank operations staff, on clickable prototypes of: the decision flow (U4, U5), the control panel (U11), and the layout builder (U1). It can run alongside wave 0. What changes as a result is recorded in the progress log before waves 1–3 are built. | — |
| **1. Deciding** | U4, U5, U6 (for these screens), U10 (fixes for these screens) | • Pay and Return buttons; a reason only on Return.<br>• The check return-reason catalogue, seeded with common reasons; its management screen comes in wave 2.<br>• Deciding several items at once.<br>• "Decide now" and the countdown banner.<br>• The exceptions list as cards on phones; the detail page reordered.<br>• All tested in the embedded frame. | The decision journey: desktop, phone (iOS Safari, Android Chrome), embedded, screen reader and keyboard |
| **2. Settings** | U11, U3, U10 (fixes for these screens) | • The control panel at bank and customer level.<br>• Reminders before the cutoff, with their setting built directly as a tile, never as a card.<br>• The return-reason catalogue's management tile. | The control panel and its modals; reminders received by email and SMS |
| **3. Uploads** | U1, U2, U6 and U10 (for these screens) | • Issue-file layouts and the layout builder.<br>• Fixing failed rows in the browser; drag and drop.<br>• The upload pages on phones and with a screen reader. | The upload journey, from a sample file to a corrected import |
| **4. Bank staff and operator** | U7, U8, U9 | • The customer hub.<br>• Bank-granted support access.<br>• The operator view (using U11's control-panel pattern at the platform level). | The customer hub; granting and using support access; the operator view |

### Why this order

- **Wave 0** fixes the one place the UI misleads today: the page-only filter. It also gives
  every later wave its components and automatic accessibility checks.
- **Wave 1** directly cuts missed cutoffs. If reminders are wanted sooner, U3's sending logic
  is backend-only and can move into wave 1; its setting still waits for wave 2's tile.
- **Wave 2** before **wave 3**, so the layouts (U1) and their settings land in the control
  panel rather than in another card.
- **Wave 4** last: support access (U8) has to exist before the operator view (U9) can link
  into a bank.

## Decisions

| # | Question | Decision |
|---|---|---|
| U-D1 | Impersonation for platform support? | No. Bank-granted, time-boxed, audited support access (U8). |
| U-D2 | Several decisions at once: are dual control and SoD relaxed? | No. Each item is checked as if decided alone; the batch only saves clicks. |
| U-D3 | Check return reasons: free text or a catalogue? | A catalogue per bank, seeded with common reasons, like ACH. The API still accepts free text. |
| U-D4 | A file fixed in the browser: replace the original? | No. The original stays as received and signed; fixes are separate, audited rows. |
| U-D5 | Settings: modals for everything? | No. Modals for small settings, and full pages for lists and workflows. Every modal also has its own URL, and works as a plain page without JavaScript. |
| U-D6 | Build phase by phase, or in waves? | In waves by screen area: one merge and one manual pass per wave, with shared components and the automatic accessibility check first. |

## Progress log

(Newest last.)
- 2026-10-10 Plan written from the outside UX analysis, checked against the code. Nothing
  built yet.
- 2026-10-10 Added U11, a control panel replacing the settings and admin card grids.
- 2026-10-10 Added U12 (lists, navigation, shared components) and reorganised the order into
  five waves by screen area, with one round of user testing before wave 1.
- 2026-10-10 Wave 0 built (M2): U12 in full for issued items, paid items, ACH and exceptions (other paginated tables say "Filter this page only"); the axe check covers ten main pages, baseline 0 after fixing pager contrast. The manual screen-reader and keyboard pass is still to do, by a person.
