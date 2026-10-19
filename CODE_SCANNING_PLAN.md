# Code scanning plan

Resolving the open code-scanning alerts on `main`, and keeping them resolved. Written
2026-10-19 from the alerts as they stand (`gh api repos/chaffed/OpenClear/code-scanning/alerts`).

## Where things stand

**157 open alerts**: 116 from CodeQL and 41 from Bandit (its SARIF is uploaded with every
finding, though only high-severity ones fail the build). The "52" quoted earlier counted only
CodeQL's medium alerts before 1.24 and 1.25 added more.

| Rule | Count | Severity | Verdict |
|---|---|---|---|
| `py/path-injection` (`storage.py`) | 8 | high | **A real vulnerability** (below) |
| `py/url-redirection` | 86 | medium | False positives: fixed `/ui/...` paths with typed ids and quoted messages, or a user-supplied target already checked by `safe_next_path`. CodeQL doesn't recognize either. |
| `py/stack-trace-exposure` | 10 | medium | **8 real**: WebAuthn errors, for bank people and platform staff, include the library exception's text. The other 2 are curated messages |
| `py/cookie-injection` | 3 | medium | **2 worth fixing** (device and CSRF cookies set again from the value the browser sent, unchecked); the theme cookie is allowlisted |
| `py/weak-sensitive-data-hashing` | 3 | high | False positives: SHA-1 is HIBP's format (not storage), HMAC-SHA256 pre-hashes before bcrypt, and one is a test |
| `py/polynomial-redos` | 2 | high | Bounded by a 320-character check first, but simpler to replace the regexes |
| `js/polynomial-redos` (TypeScript SDK) | 2 | high | The SDK's own base URL and the `Link` header: low risk, simple to bound |
| `py/log-injection` | 1 | medium | False positive: logged with `%r`, and text logs escape control characters (#9) |
| `py/overly-permissive-file` (`zap_scan.py`) | 1 | high | Real but harmless: `chmod 777` on the report folder; ZAP runs as root, so it isn't needed |
| Bandit B105, B106 | 32 | low | False positives: token-type names (`"bearer"`, `"refresh"`) and the like; gitleaks covers real secrets |
| Bandit B311, B101, B405 | 5 | low | Jitter `random`, one `assert` in a file writer, `xml.etree` used only to write XML |
| Bandit B704, B610 | 4 | medium | `Markup()` on HTML already cleaned by nh3; a method named `extra` mistaken for Django's |

### The vulnerability: attestation evidence names escape the storage folder

`attestation_service.store_files` builds the storage key from the uploaded file's own name
(`f"{tenant_id}/attestations/{hex}-{name}"`), and `LocalStorage._path` joins the key to the
storage folder without checking where it lands. A name such as `x/../../../../../escaped.txt`
writes outside the folder: reproduced on 2026-10-19 (the file landed at the repository root;
removed). Reachable by anyone with `compliance:attest`, which isn't masked for customer-scoped
sessions, so **a bank's business customer** can do it, through the web page or the API.

With local storage (the default), the app's own user can then write into any bank's files:
replace another bank's check images or decision files, or a model artifact, which is a joblib
(pickle) file. The registry checks each artifact's SHA-256 before loading it, which stops the
code-execution path; the cross-bank tampering stands. S3 and Azure storage treat `..` as part of
an object's name, so they're not affected. Severity: **high**.

## Decisions (recommended)

- **CS-D1. The traversal is handled as a vulnerability, first:** fixed in a patch release
  (1.25.1) with a private advisory (SECURITY.md; ADR 0002), before anything else here. The fix
  is in two places: `LocalStorage` refuses any key that resolves outside its area (the real
  control, for every caller), and `store_files` keeps only the file name's last part. A test
  proves both, through the web and the API, as a customer's user.
- **CS-D2. Fix what's real; dismiss false positives with a written reason, in bulk, after the
  code makes the reason obvious.** A dismissal says why (`gh api ... -f dismissed_reason="false positive" -f dismissed_comment=...`).
- **CS-D3. Redirects go through one helper,** `web/redirects.py::see_other(path, **query)`:
  `path` must be a fixed local path (checked with `safe_next_path`'s rules), and the query is
  `urlencode`d. A test fails if any route builds a `RedirectResponse` itself. Then the 86 alerts
  are dismissed, each pointing at the helper. (A CodeQL barrier model for the helper is tried
  first; if CodeQL takes it, the alerts close on their own.)
- **CS-D4. Error responses show curated text only.** `WebauthnError` gets fixed messages; the
  library's exception goes to the log, not the response.
- **CS-D5. A cookie is set again only from a value in the expected shape** (the device token,
  the CSRF token); otherwise a new one is generated.
- **CS-D6. Bandit uploads medium and above to code scanning** (`--severity-level medium` on the
  SARIF). The low-severity notes stay visible locally; the remaining medium ones get inline
  `# nosec` comments with their reason. The `assert` (B101) becomes an explicit check, since
  asserts vanish under `python -O`.
- **CS-D7. No more high alerts reach `main` unseen.** The ruleset already blocks a pull request
  with a high CodeQL alert, but the maintainer's direct pushes bypass it, which is how the
  traversal sat open. Two changes: release work goes through a pull request (the gate runs), and
  the CodeQL workflow fails when the analysis has a new error-level or high-severity result, so
  a direct push goes red too.

## Stages

| Stage | What | Release |
|---|---|---|
| **CS0. The traversal** | `LocalStorage` containment; `store_files` name; tests (web, API, customer user, every area); a draft GHSA with the reproduction; then the advisory published with the release. Check every other `storage.put*` caller's key while there (all others are generated). | 1.25.1 |
| **CS1. Real fixes** | WebAuthn messages (CS-D4); the device and CSRF cookie checks (CS-D5); the email regexes replaced with a split-based check; the SDK's two regexes bounded (and the SDKs regenerated); the `chmod 777` removed; the B101 `assert`. | next minor |
| **CS2. Redirects** | `see_other` (CS-D3), every router moved to it, the guard test; try the CodeQL barrier model, else dismiss the 86 with the reason. | next minor |
| **CS3. The rest** | Bandit's SARIF at medium and up, `nosec` with reasons for B704 and B610 (CS-D6); dismiss the weak-hashing, log-injection and two remaining stack-trace alerts with reasons. | next minor |
| **CS4. Keeping it at zero** | CodeQL fails the build on new high or error results (CS-D7); CLAUDE.md: "release work goes through a PR"; a line in OWASP_PLAN.md's pipeline decisions. | next minor |

After CS3 the open count should be zero, with every dismissal explained in its alert.

## Progress log

- 2026-10-19: Plan written from the 157 open alerts. The path traversal (CS0) found and
  reproduced while reviewing `py/path-injection`.
- 2026-10-19: CS0 built on `cs0`: `storage.safe_key` on every write (all backends, plugins
  included) and a resolve-and-contain check in `LocalStorage`; `storage.safe_name` for the
  attestation and bulk-upload names; tests through the web as a customer's user and through the
  API, which fail on the old code. Draft advisory GHSA-55j4-4cj6-8j2f (high, CWE-22, 1.19.0 to
  1.25.0, fixed in 1.25.1).
- 2026-10-19: CS1 on `cs1`: WebAuthn failures answer with fixed text and log the library's
  (bank and platform); the device and CSRF cookies are set again only from a value shaped like
  ours (`web/security.py::token_shaped`); `email_change_service.looks_like_email` replaces both
  email regexes; the TypeScript SDK's two regexes replaced by plain string handling; the
  returns file's `assert` is a real check; `zap_scan.py` no longer opens its folder to everyone.
- 2026-10-19: CS2 on `cs1`: `web/redirects.py` (`see_other`, `to_identity_provider`); all 264
  `RedirectResponse(...)` calls rewritten by an AST script; `tests/test_web/test_redirects.py`
  fails on any new one. Whether CodeQL takes the helper as a barrier shows on the first PR run;
  otherwise the 86 alerts are dismissed pointing at it.
- 2026-10-19: CS3 and CS4 on `cs1`: Bandit's SARIF at medium and up, `nosec` with reasons for
  B704 and B610 (Bandit then finds nothing at medium); the CodeQL workflow fails on an open
  high or critical alert for its language (`.rule.severity == "error"` was dropped from the
  filter: CodeQL marks medium security queries as errors too); CLAUDE.md and OWASP_PLAN.md
  (OW-D8a). Left for after the push: dismissing the false positives with reasons, and checking
  which path-injection alerts CodeQL still reports behind the new check.
- 2026-10-19: 1.25.1 released through PR #23 (every check green, the new CodeQL gate included);
  GHSA-55j4-4cj6-8j2f published. On `main`, CodeQL kept the original eight path-injection alerts
  open at their new lines, and the new gate failed the merge's run on them: dismissed as fixed,
  re-run green. The 86 redirect alerts closed; the last 12 (WebAuthn messages, token-shaped
  cookies, the theme allowlist, a curated message) dismissed with reasons. **Open alerts: 0, from
  157.** The plan is complete.
