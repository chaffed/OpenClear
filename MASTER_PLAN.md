# Master plan: edge, security logging and user experience

This plan sequences three plans so that code is changed as few times as possible and each
area is tested as few times as possible:
- WAF_PLAN.md, running behind Cloudflare, Akamai and other edges;
- SIEM_PLAN.md, security logging and SIEM integration;
- UX_PLAN.md, user experience.

Each plan keeps its own phases, decisions and progress log. This plan says which of their
waves are built together, in what order, and how each is tested.

**Status: M1, M2 and the error pages merged; M3 built, not yet merged.**

## How testing is kept down

- **One merge per stage.** Each stage below is one merge to `main`, with one run of the
  three full suites (SQLite, Postgres, Linux 3.11) and one manual pass over what it changed.
  Within a stage, each commit runs only the tests it touches. Seven stages replace the
  fourteen or so merges the plans' phases would take one at a time.
- **Waves that touch the same code go together.**
  - WAF wave A and SIEM wave A both rework request handling, sign-in and the action log.
  - UX wave 2 and SIEM wave B both add to the control panel and the Controls page.
- **Foundations first.** Built before anything uses them:
  - the request context (client IP, request id, who);
  - the shared UI components (dialog, confirmation, toast, busy state);
  - the automatic accessibility check.
- **Each outside check runs once.** These are slow, or need people or accounts. Each runs at
  the point where everything it checks exists:
  - user testing with AP clerks and bank staff;
  - a real Splunk and syslog receiver;
  - a real Cloudflare zone.
- **At most one database migration per stage**, so upgrade and downgrade are tested once
  each time.
- **Releases group stages.** Four releases instead of seven. Each one is a deploy of the demo
  and sandbox and a smoke test; the suites have already run for every stage in it.

## Stages

| Stage | Plan waves | What lands | Migration | Manual pass | Outside checks |
|---|---|---|---|---|---|
| **M1. Request context and security events** | WAF A; SIEM A | • One middleware for each request's context: client IP (trusted header or hop count), request id, who. Everything reads it.<br>• Rate limits per person or API client; the edge secret; the source IP and request id in action-log metadata.<br>• The "Edge in front" control; uvicorn proxy trust and keep-alive; static cache headers; the sandbox's proxy setting fixed.<br>• JSON logging; the security event catalog, emitter, outbox and hooks (sign-ins, failures, lockouts, MFA, tokens and the rest).<br>• Pull APIs for security events and the action log; `openclear events tail`; SDKs regenerated. | `security_event` (the outbox) | Sign-ins (good, bad, lockout, MFA, token failure) checked against the feed, the JSON logs and the action log. The demo and sandbox behind Fly's proxy show the right client IP. | — |
| **M2. Foundations and long requests** | UX 0; WAF B | • Lists that search, filter, sort and export on the server; global search; navigation by work; item page titles; back to the filtered list; empty states.<br>• The shared dialog, confirmation, toast and busy-state components.<br>• The axe accessibility check in the suite, and a baseline audit.<br>• Replay, evidence export and action-log verification become jobs, polled with the new components. The Controls page uses the last verification. SDKs follow the `202` answers. | None. Verification and export runs are kept as `control_run` kinds, as replay and anomaly capture already are. | The lists and navigation; Model risk, Controls and the export. | **User testing round** (see below) runs during M1 and M2 |
| **M3. Deciding** | UX 1 | • Pay and Return buttons; a reason only on Return; the check return-reason catalogue (seeded).<br>• Deciding several items at once.<br>• "Decide now" and the countdown banner.<br>• The exceptions screens on phones and in the embedded frame, and their accessibility fixes.<br>• Optionally, U3's reminder sending, with the bank default from configuration until M4 adds its tile. | `check_return_reason` | The decision journey once: desktop, iOS Safari, Android Chrome, the embedded frame, screen reader and keyboard. | Findings from user testing applied first |
| **M4. Control panel, reminders and shipping** | UX 2; SIEM B | • The control panel at bank and customer level, replacing the Settings and Admin grids.<br>• Reminders before the cutoff, with their tile.<br>• The return-reason catalogue's tile.<br>• Security event destinations: the installation's from configuration, each bank's as a tile. Delivery reuses the webhook pattern (job queue, retry schedule, per-destination cursor).<br>• The "Security events delivered" control; counts in the evidence export; the SIEM guide and its crosswalk row. | Reminder settings and `security_event_destination`, in one migration | The control panel and its modals; reminders by email and SMS; adding a destination and its test event. | Splunk (container) through HEC and syslog, and a syslog-ng relay with TLS, once. |
| **M5. Uploads** | UX 3 | • Issue-file layouts and the layout builder; their tiles.<br>• Fixing failed rows in the browser; drag and drop.<br>• The upload pages on phones, and their accessibility fixes. | Layouts and upload corrections | The upload journey: sample file, layout, import, correcting failed rows. | — |
| **M6. Bank staff and operator** | UX 4 | • The customer hub.<br>• Bank-granted support access.<br>• The operator view, as a control panel at platform level. It shows every bank's controls, edge state and event delivery, which by now all exist. | `support_access_grant` | The customer hub; granting, using and expiring support access; the operator view. | — |
| **M7. Edges** | WAF C | • The edge check script.<br>• The edge deployment guide, with WAF exclusions and bot-management bypasses for every path. By now all paths exist: layouts, support access, the operator view.<br>• Fixes for anything the real zone shows. | None expected | The edge check against a stub edge (a local proxy imitating Cloudflare and Akamai), in the suite and CI. | A real Cloudflare zone (and Akamai) later, to confirm; not needed to build M7 |

## Releases

| Release | Stages | Why there |
|---|---|---|
| 1.10 | M1, M2 | Security visibility, and the fix for the misleading page-only filter. Nothing here needs the user testing results. |
| 1.11 | M3, M4 | Deciding and the control panel together: the two biggest changes users see, announced once. |
| 1.12 | M5, M6 | Uploads, then the staff and operator tools. |
| 1.13 | M7 | The edge check and guide, tested against a stub edge; confirmed against a real zone when one is set up. |

## Code shared across plans (built once)

| Built in | What | Used by |
|---|---|---|
| M1 | The request context (client IP, request id, who) | Rate limits, the action log, JSON logs, security events, error pages, the edge control |
| M1 | The security event emitter | Every sign-in and security hook; later the reminders' and support access's events |
| M2 | Dialog, confirmation, toast, busy state, polling for jobs | Long-request jobs (M2), deciding several items (M3), control-panel modals (M4), the layout builder (M5), support access (M6) |
| M2 | Jobs for long requests, kept as `control_run` kinds | Replay, evidence export, verification; the Controls page reads the last result |
| M2 | Server-side list queries | Every list; the customer hub (M6) is a list with more columns |
| M2 | The axe check in the suite | Every later stage, automatically |
| M4 | The control panel | Reminders, return reasons, event destinations (M4), layouts (M5), support access (M6), the operator view (M6) |
| M4 | Delivery with retries and a cursor, as webhooks already have | Event destinations; reused, not rebuilt |
| M1, M2, M4 | New controls (edge, verification age, event delivery) | The Controls page and the evidence export pick them up with no further change |

## What's needed from you, and when

| When | What |
|---|---|
| During M1–M2 | **User testing:** about five sessions each with AP clerks and bank operations staff, on clickable prototypes of the decision flow, the control panel and the layout builder. I prepare the prototypes; the sessions need people. |
| Whenever convenient | **A test hostname on a Cloudflare zone**, to confirm M7 against a real edge. Parked: M7 is built and tested against a stub edge without it. |
| Each release | Approval to push, tag, release and deploy, as now. |

Nothing else blocks a stage. Without user testing, M3–M5 can still be built, with the
prototypes judged by us alone, but that's the riskiest part to guess. Without a Cloudflare
zone, M7 is still built and tested, against a stub edge; only the final confirmation waits.

## Decisions

| # | Question | Decision |
|---|---|---|
| M-D1 | Build the three plans one after another, or interleaved? | Interleaved by the code they touch. WAF A with SIEM A, UX 2 with SIEM B, so shared code is changed and tested once. |
| M-D2 | Real-zone checks (WAF W0) first, or last? | Last (M7). Every edge setting defaults to today's behaviour, and checking once, when every path exists, covers them all. |
| M-D3 | New tables for job runs (evidence export, verification)? | No: `control_run` kinds, as replay and anomaly capture already use. |
| M-D4 | Release after every stage? | No: four releases. Each stage's suites already ran, so a release adds only deploy and smoke. |

## Progress log

(Newest last.)
- 2026-10-10 Master plan written. The WAF, SIEM and UX plans are reorganised into waves, and
  combined here into seven stages and four releases. Nothing built yet.
- 2026-10-10 M1 built on branch `m1-request-context`: the request context (client IP from a trusted edge header or hop count, request ids, who), per-person rate limits, the edge secret, the source IP and request id in action-log metadata, the edge control, keep-alive, static caching and a JSON 413 (WAF wave A); JSON logs, the security event catalog, emitter, `security_event` table and hooks, and the pull feeds (SIEM wave A).
- 2026-10-10 M1 merged into `main` at `c213b777`. All three suites were green: SQLite 1460 passed, Postgres 1485, Linux 3.11 1447.
- 2026-10-10 M2 built on branch `m2-foundations`: server-side list search, sort and CSV export (issued items, paid items, ACH, exceptions), global search, nav by work with an Exceptions count, Action Log naming, item page titles, back to the filtered list, empty states, shared confirm/toast/busy/poll components, the axe check (0 problems after fixing pager contrast) (UX wave 0); replay, evidence export and verification as background jobs on `control_run`, daily verification, Controls reading the latest verification (WAF wave B). No migration.
- 2026-10-10 M2 merged into `main`. The three suites at `23272e41` each had one failure, the same test (the Approvals badge test also matched the new Exceptions badge); fixed in the test at `b3847d02` and rerun green on SQLite and Postgres. SQLite 1471 passed, Postgres 1496, Linux 3.11 1457 (plus that test).
- 2026-10-10 Real Cloudflare zone parked; M7 will use a stub edge (WAF_PLAN.md, wave C), with the real-zone run as a later confirmation.
- 2026-10-11 Before M3: error pages for every HTTP error on web pages, 503 for database outages, `/health` checking the database (`e6d6338b`; SQLite 1479, Postgres 1504, Linux 1465). Tests now run in parallel (pytest-xdist, a Postgres database per worker) with live logs via `scripts/run_tests.sh`; Colima raised to 6 CPUs and 10 GB. All three suites running side by side: SQLite 10 min, Postgres 13 min, Linux 9 min (they took 22–26 min each, one at a time).
- 2026-10-11 M3 built on branch `m3-deciding` (the prototype's designs accepted as they stand, before user testing): Pay/Return buttons, check return reasons (`check_return_reason`, seeded), deciding several at once (`POST /ui/exceptions/bulk`, per-item rules and transactions), decide-now landing and banner for customer users, the exception page reordered, P/R/J/K keys, cards and a sticky decision bar on phones, WCAG-passing button colours; fixed UTC times from SQLite in the browser.
