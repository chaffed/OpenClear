## What and why

<!-- What changes, and the issue or plan step it's for. -->

## Checks

- [ ] Tests added or updated; the suites pass (`scripts/run_tests.sh`).
- [ ] `CHANGELOG.md` under "Unreleased", for anything a user would notice.
- [ ] New tables or columns in the data dictionary; a new table with `tenant_id` has its RLS policy.
- [ ] A new outside system, trust boundary or data flow is in `threat_model/openclear.py`.
- [ ] New security behaviour has a test marked `@pytest.mark.asvs("x.y.z")`, and its status in `docs/security/asvs-status.csv`.
- [ ] A new dependency: what it's for, why not one we already have or the standard library, and that it's maintained:

<!-- New dependencies, one line each, or "none". -->

## Security (OWASP WSTG)

Only if this touches sign-in, sessions, permissions or uploads. Tick what you checked, and
say what in a line underneath. The [WSTG](https://owasp.org/www-project-web-security-testing-guide/)
section is in brackets.

**Sign-in** (WSTG-ATHN)
- [ ] Every new way in needs the same factors as the old ones, and can't skip a required security key (ATHN-04).
- [ ] Failed attempts count toward the lockout; errors don't say whether the account exists (ATHN-03, IDNT-04).
- [ ] A one-time link or code is random, stored only as a hash, expires, and works once (ATHN-09).

**Sessions** (WSTG-SESS)
- [ ] New cookies use `cookie_name()` (`__Host-`/`__Secure-`), HttpOnly, SameSite (SESS-02).
- [ ] A change to someone's credentials, factors or address ends their other sessions or tells them (SESS-06).
- [ ] Every state-changing form or call checks CSRF (SESS-05).

**Permissions** (WSTG-ATHZ)
- [ ] Every new route checks a permission, and reads only through the bank's and customer's scope (ATHZ-02).
- [ ] An id in a URL or body is looked up within the caller's scope, never trusted (ATHZ-04, IDOR).
- [ ] Customer-scoped sessions can't reach it unless meant to (`CUSTOMER_SCOPE_MASKED_PERMISSIONS`).
- [ ] Under Postgres RLS: a test in `tests/test_postgres/test_rls_*.py` if it reads across people or banks.

**Uploads** (WSTG-BUSL-08, BUSL-09)
- [ ] Only the file types read are accepted, checked by content, not name; sizes are limited.
- [ ] The file goes through the malware check (automatic for HTTP uploads; call `malware_service.check_bytes` otherwise).
- [ ] It's never served back with the type the uploader declared.

**Data in URLs** (WSTG-INFO)
- [ ] No account number, token or other sensitive value in a URL (ASVS 14.2.1).
