# Review fixes plan

Fixes for what the 2026-10-19 two-axis review of `v1.25.0...HEAD` found (code scanning
CS0–CS4, OW5, release 1.25.1). Standards: no breaches of a documented rule, six smells. Spec:
four requirements built only in part, three that look wrong. Every item points back to the
finding it closes.

None of it is a vulnerability. Every redirect is already checked by `is_local`, and every
storage key by `safe_key`. This is cleanup, tests the plan promised, and one release step in the
wrong order. It ships in the next minor release.

## Findings and what happens to each

| # | Finding | Axis | Stage |
|---|---|---|---|
| F1 | `see_other(url, status_code, headers)`: no caller passes `status_code` or `headers` (Speculative Generality) | Standards | RF1 |
| F2 | 138 hand-built `?flash=`/`?error=` query strings, and 8 routers with their own `_back`/`_domain_back` wrappers (Duplicated Code) | Standards | RF1 |
| F3 | CS-D3 asked for `see_other(path, **query)` with the query `urlencode`d; that's not what was built | Spec | RF1 |
| F4 | `is_local` (`web/redirects.py`) copies `safe_next_path` (`web/security.py`) | Spec | RF1 |
| F5 | `saml.py:124` sends `to_identity_provider(redirect or "/ui/login")`, so a local path goes through the identity-provider helper (Mysterious Name) | Standards | RF1 |
| F6 | The guard test only catches a bare `RedirectResponse(` call | Spec | RF1 |
| F7 | The WebAuthn failure (comment, log line, "didn't check out" message) is written out four times, in two modules (Duplicated Code) | Standards | RF2 |
| F8 | `customer_people_service` imports `looks_like_email` from `email_change_service`, inside a function (Feature Envy) | Standards | RF2 |
| F9 | The new `see_other` imports sit out of order | Standards | RF1 and RF2, in the files they touch anyway |
| F10 | CS-D1's "through the web and the API, as a customer's user": the API test signs in as the bank's admin | Spec | RF3 |
| F11 | CS0's "every area": only `audit_archives` is tested, and nothing tests `LocalStorage._path`'s own containment check | Spec | RF3 |
| F12 | `release_metrics.py` runs at release step 1 but counts only *published* advisories, and the advisory is published at step 3, so a security release always records "Advisories fixed: none" | Spec | RF4 |
| F13 | CS-D7 still says the gate fails on "error-level" results; the workflow checks high and critical security severity only | Spec | RF5 |
| F14 | The OW5 stage row leaves out the TPRM pack and docs-site changes it made | Spec | RF5 |
| F15 | `.github/rulesets/main.json` is untracked | Spec (note) | RF5 |
| — | CS1–CS4 went out in patch 1.25.1, not "next minor" | Spec | No change: already shipped, and safe. Noted in the log |
| — | `safe_key` refuses `..` on S3 and Azure too, though only local storage was affected | Spec | No change: one rule for every backend and plugin was the intent (CS0 log) |
| — | `sdks/typescript/src/client.ts` carries an Apache-2.0 header | Standards | No change: the SDK is Apache-2.0 on purpose (`sdks/typescript/LICENSE`, `package.json`) |

## Decisions (recommended)

- **RF-D1. `see_other(path, **query)`, as CS-D3 asked.** `path` is a local path, which may
  already carry a fixed query (`/ui/exceptions?status=all`). The keyword arguments are
  `urlencode`d and joined with `?` or `&`; `None` values are dropped. It always answers 303.
  `status_code` and `headers` go (F1), and so does `to_identity_provider`'s `status_code`. The
  parameter is renamed `path`, which is what it holds. Calls become
  `see_other("/ui/admin/sso", flash="Saved.")` and `see_other(_BASE, error=str(exc))`.
- **RF-D2. The eight `_back` wrappers go.** With RF-D1, `see_other(_BASE, flash=msg)` is as short
  as `_back(msg)`, and each wrapper only fixed a path and picked a key. The
  `error: bool` flag becomes the `error=` or `flash=` keyword at each call site.
- **RF-D3. One local-path rule.** `is_local` stays in `web/redirects.py` as the rule.
  `safe_next_path` becomes `path if is_local(path) else default`, and its docstring moves to
  `is_local`. `web/security.py` imports from `web/redirects.py`, which imports nothing from it,
  so there's no import cycle.
- **RF-D4. SAML sign-out splits its two cases:** `to_identity_provider(redirect)` when the
  identity provider gave a URL, otherwise `see_other("/ui/login")`. `to_identity_provider` then
  only ever leaves the site, as its name says.
- **RF-D5. The guard test looks at every way of making a redirect:** any call whose name or
  attribute is `RedirectResponse`, any import of `RedirectResponse` under another name, and any
  `Response(..., headers={"location": ...})` or `.headers["location"] =` outside
  `web/redirects.py`. SCIM's `location` headers (`services/scim_service.py:179,376`) are on 201
  responses, not redirects: the test allows those two by name.
- **RF-D6. One WebAuthn failure helper.** `webauthn_service.refused(step: str, exc: Exception) ->
  WebauthnError` logs the library's message at info and returns the error with fixed text
  (`f"{step} failed: the security key's response didn't check out. Try again."`). All four sites
  call `raise refused("Registration verification", exc) from exc`, and so on. The platform service
  already imports `WebauthnError` from there.
- **RF-D7. `looks_like_email` moves to `services/email_address.py`**, a small module of its own.
  It's a rule about an email address, not part of the email-change flow. Both services import
  it at the top of the file, and `email_change_service` doesn't re-export it, since nothing
  else uses it. (The plan first said `domain/`, but that package holds only ORM models.)
- **RF-D8. Metrics count the release's draft advisories too.** `advisories()` asks for
  `state=draft` and `state=published`, so step 1 already sees the advisory the release fixes.
  The metrics file names it, and it reaches `main` with the fix, minutes before the release.
  ADR 0002 keeps a gap private *until it's fixed*, and step 2 is the fix landing, so this is
  within it. Step 3 adds "publish the release's advisories" before creating the GitHub release,
  so the notes link to a public page. The alternative, re-running metrics after the tag, is
  what happened in 37533086: the tagged file is wrong. No new dependency.
- **RF-D9. Correct the plan text without rewriting history.** CS-D7 gets a dated note: "As
  built: high or critical security severity; CodeQL marks medium security queries as errors,
  so the error filter was dropped (progress log)". The OW5 row gains the TPRM pack and docs-site
  lines. The 1.25.1 tag and its metrics file stay as they are.
- **RF-D10. Commit the ruleset as the reviewed copy, once it matches GitHub.** Compare
  `.github/rulesets/main.json` with `gh api repos/chaffed/OpenClear/rulesets/<id>`. If they
  match, commit it, so a change to the gate shows in a diff. If they don't, update the file
  from GitHub first. The maintainer's `always` bypass stays: CS-D7's answer was "release work
  goes through a PR" plus a CodeQL workflow that turns red on a direct push, and both are in place.

## Stages

| Stage | What | Closes |
|---|---|---|
| **RF1. Redirects** | `see_other(path, **query)` (RF-D1); every flash, error and `next` call site rewritten by an AST script like CS2's, the 8 wrappers removed (RF-D2); `safe_next_path` on `is_local` (RF-D3); SAML sign-out split (RF-D4); the stronger guard test (RF-D5); imports sorted in the files touched. Tests: `see_other` with a fixed query plus keywords, `None` dropped, `&`/`#`/spaces/non-ASCII encoded and read back by the page; SAML sign-out with and without an identity-provider URL. Whatever expects `%20` where `urlencode` now writes `+` gets updated. The full sqlite suite before moving on, since the change reaches about 140 call sites. | F1–F6, F9 |
| **RF2. Two small moves** | `webauthn_service.refused` used at all four sites (RF-D6); `domain/email_address.py` (RF-D7). The existing WebAuthn and email tests must pass unchanged, which shows the behaviour stayed the same. | F7, F8, F9 |
| **RF3. CS0's missing tests** | `test_through_the_api_too` signs in as the customer's attester (the `cfo@cs0-customer` user the web test makes), not `users["admin"]`. Mark it `asvs("5.3.2")` like the web test. A storage test parametrized over `storage.AREAS`: `LocalStorage.put` refuses a traversal key in every area. A symlink test: a link inside the area pointing outside, then `put("<tenant>/link/x.txt")` raises `UnsafeKey`, which only `_path`'s resolve check catches. | F10, F11 |
| **RF4. Release metrics** | `advisories()` reads draft and published (RF-D8); a test with `subprocess.run` faked, showing a draft advisory patched in the version is counted and one for another version isn't; CLAUDE.md release step 3: publish the advisory, then the release. | F12 |
| **RF5. Plan text and ruleset** | CS-D7 note and the OW5 row (RF-D9); the ruleset compared and committed (RF-D10); CHANGELOG "Unreleased": none of this is user-facing except the encoding of flash messages, so a single "Internal" line. | F13–F15 |

RF1 is the only stage with risk: it touches most routers. RF2–RF5 can go in any order after it,
or alongside it on separate branches. All five go through one pull request, so the ruleset's
checks run (CLAUDE.md). Before merging: the three suites, Bandit and the dependency audit, as usual.

## Progress log

- 2026-10-19: Plan written from the code review of `v1.25.0...HEAD` (Standards and Spec axes).
  Findings F1–F15 checked against the code; F10 confirmed by reading
  `tests/test_compliance/test_attestation_files.py:69`.
