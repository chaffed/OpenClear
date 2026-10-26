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
- **TS-D8. A prebuilt CI image, so CI never runs `apt`** (the product owner, 2026-10-26). An image
  with Tesseract, WeasyPrint's libraries, the Postgres client and Chromium's system libraries,
  built from a Dockerfile in the repo, published to GitHub's container registry and pinned by
  digest in `tests.yml`. It's rebuilt weekly and when its Dockerfile changes; each build gets an
  SBOM and a vulnerability scan, like the release image. The browser tests use the Google Chrome
  already on GitHub's runners (Playwright's `chrome` channel), chosen in `tests/conftest.py` for CI
  only; locally they keep Playwright's own Chromium.
- **TS-D6. Not now:** larger paid runners; skipping suites for docs-only changes (the required
  checks must still report, which needs care).
- **TS-D7. Measure.** Each stage records CI's wall time before and after in the log below. Aim:
  CI in 4–5 minutes, the local Postgres suite in about 4.

## Stages

| Stage | What | Status |
|---|---|---|
| TS1 | The `browser` marker; browser tests in one CI job; `--dist worksteal` | Done (#47) |
| TS2 | Split the viewport and accessibility checks by page group, keeping their baselines | Done (#48) |
| TS3 | Shard every CI suite (both Python versions and Postgres) in three | Done (#52) |
| TS4 | Short timeouts; measure and record | |
| TS5 | The prebuilt CI image (TS-D8): its Dockerfile and weekly build with SBOM and scan; `tests.yml` runs in it; Chrome for the browser tests | |

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
- 2026-10-24: TS2. The viewport check runs as 12 tests (each width in three parts, pages dealt in
  turn) and the accessibility check as 10 (each view in two). **Finding:** the control pages share
  state (opening one records the control results the others show), so they always run together,
  in order (`TOGETHER`). Rewriting the baselines from the parts leaves the viewport baseline, report
  and screenshots unchanged, and the axe baseline's problems unchanged. The longest browser test
  fell from 26 s to 14 s; locally the suite's total is unchanged (136 s), since all eight workers
  are already busy. CI times to follow from the PR.
- 2026-10-26: TS2 in CI: the 3.13 job's tests took 9 min 1 s (8 min 47 s before), so no gain: a
  4-core runner queues the smaller pieces too. **Finding:** CI is slow because each runner is
  small, not because of any test (the 3.11 job runs no browser test and still takes ~6 min). So
  TS3 shards every suite, not only Postgres.
- 2026-10-26: TS3. `OPENCLEAR_TEST_SHARD=k/n` runs one shard: `tests/conftest.py` gives each test
  file whole to a shard, largest first to the lightest, a browser test weighing 25. Locally the
  three shards run 794, 701 and 750 tests (37–59 s each), together exactly the full suite. CI runs
  each suite in three shards side by side; the checks the ruleset requires (`test (3.11)`,
  `test (3.13)`, `test-postgres`) are now summary jobs that pass only when all their shards did,
  so the ruleset is unchanged. Not done: TS-D4's trim (the shards still need Tesseract and the PDF
  libraries, for OCR and PDF tests), and sharding `run_tests.sh postgres` (its workers create test
  databases by name, so local shards side by side would collide).
- 2026-10-26: TS3 in CI (#52): the tests take 1.9–4.1 min per shard, against 6–8½ min per suite
  before. But the run took ~14¾ min: setup, not tests. `apt-get` took 0.1–0.4 min in most jobs and
  2.2–2.9 min in three, and the Chromium install (`--with-deps`, which also runs `apt`) 0.3 min in
  most and 6.2 min in one: a slow Ubuntu mirror, random per runner, and nine jobs make one slow job
  likelier. Typical runs should take about 5 min; TS5 removes `apt` from CI to end the variance.
