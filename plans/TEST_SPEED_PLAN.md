# Faster tests

CI takes about 10½ minutes, the Postgres job being the longest: over a minute installing system
packages and 8 running tests. The two SQLite jobs take about 6 each. Locally the SQLite suite takes
2½ minutes and the Postgres suite about 8½.

A few browser tests dominate. Each is one long unit, so parallel workers wait on whichever one
drew it:

| Tests | Each | Total |
|---|---|---|
| Viewport checks (4 widths) | 14–26 s | ~80 s |
| Accessibility checks (5 views) | 4–15 s | ~52 s |
| Redis limiter failing open | 11.5 s | |
| Admin docs PDF | 9 s | |
| .NET SDK build | 7.7 s | |

## Decisions (the product owner, 2026-10-23: "add to the plan")

- **TS-D1. Browser tests once.** They test the pages, not the database or the Python version, so
  they run in the Python 3.13 job only, not the 3.11 or Postgres jobs. A marker (`browser`) selects
  them; the local suites follow the same rule.
- **TS-D2. Smaller units, better spread.** The viewport and accessibility checks are split by page
  group, so no single test runs for 20 seconds, and pytest-xdist uses `--dist worksteal` (already
  in pytest-xdist), so idle workers take queued tests from busy ones.
- **TS-D3. Shard the Postgres job** into parallel jobs (three to start), split by test file name
  in a conftest hook: no new dependency. `scripts/run_tests.sh postgres` takes the same option.
- **TS-D4. Only the system packages the tests need** in the Postgres job.
- **TS-D5. Short timeouts in tests** that wait on something absent (the Redis limiter test).
- **TS-D6. Not now:** larger paid runners; skipping suites for docs-only changes (the required
  checks must still report, which needs care).
- **TS-D7. Measure.** Each stage records CI's wall time before and after in the log below. Aim:
  CI in 4–5 minutes, the local Postgres suite in about 4.

## Stages

| Stage | What | Status |
|---|---|---|
| TS1 | The `browser` marker; browser tests in one CI job; `--dist worksteal` | In review |
| TS2 | Split the viewport and accessibility checks by page group, keeping their baselines | |
| TS3 | Shard the Postgres job and `run_tests.sh postgres`; trim its system packages | |
| TS4 | Short timeouts; measure and record | |

## Progress log

- 2026-10-23: Plan written; CI at about 10½ minutes (Postgres job), 6 (each SQLite job).
- 2026-10-23: TS1. **Finding:** CI never installed Playwright's Chromium, so the 32 browser tests
  (axe accessibility, viewports, the phone layouts) skipped themselves in every CI job and in the
  Linux suite; they only ever ran on the developer's Mac. So they weren't what made CI slow, and
  CI wasn't checking accessibility. Built: `tests/conftest.py` marks a test `browser` when it
  uses `browser_page` or starts Playwright; the Python 3.13 CI job installs Chromium and runs
  them, the 3.11 and Postgres jobs and the local Postgres and Linux suites leave them out; every
  run uses `--dist worksteal` and reports skip reasons (`-ra`). Locally the SQLite suite went from
  157 s to 135 s; without the browser tests it takes 86 s. In CI the 32 browser tests now run (the
  3.13 job: 2228 passed against 2196 in the 3.11 job). Running them showed one test-setup slip:
  `test_the_modal_in_a_browser` lacked `bypass_csp`, so Playwright's polling hit the page's CSP on
  CI's slower runner. No accessibility or layout problem showed on Linux. CI wall time stays about
  10½ minutes: the 3.13 job now does more (Chromium and the browser tests, ~10½ min), the 3.11 job
  varies 4¾–6½ min between runs, the Postgres job ~10 min. worksteal helps little on CI's small
  runners; TS2 and TS3 are where CI's time comes down.
