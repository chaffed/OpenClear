# OpenClear: notes for Claude Code

Positive pay (checks and ACH) for banks and their business customers. FastAPI, SQLAlchemy 2,
Alembic, Jinja templates, plain JS in `static/js`. Python package in `src/openclear/`.
AGPL-3.0: every source file starts with the SPDX and copyright header.

## Where things are
- `domain/` models · `services/` business logic · `repositories/` tenant- and customer-scoped queries
- `api/v1/` JSON API · `web/routers/` + `templates/` web UI · `auth/` sign-in, tokens, SSO, permissions
- `networks/` payment rails (`check/`, `ach/`; `registry.py`) · `bulk_import/` file parsers
- `db/tenancy.py` RLS binding, `TenantContext`, `memberships_elsewhere` · `db/account_scope.py` per-account access
- `workers/` job queue, scheduler, handlers · `storage.py` local, S3, Azure files
- Plans and progress: `MASTER_PLAN.md` and the `*_PLAN.md` files (each has a progress log; add to it)

## Tests
- One file: `.venv/bin/python -m pytest -q tests/path.py` (add `-n 6` for speed).
- Full suites before merging to `main`, one at a time: `scripts/run_tests.sh sqlite`, then
  `scripts/run_tests.sh postgres <commit>` and `scripts/run_tests.sh linux <commit>`.
  Results are in `.openclear-run/test-logs/<suite>.log`.
- Also before pushing, the security gates CI runs: `.venv/bin/bandit -r src -q --severity-level high`
  and `.venv/bin/python scripts/audit_dependencies.py`.
- Docker is Colima: `DOCKER_HOST=unix://$HOME/.colima/default/docker.sock`. Its containers don't
  restart on their own: `docker start pospay-pg-test oc-moov-ach oc-moov-icl`.
- Postgres tests by hand: `OPENCLEAR_TEST_DATABASE_URL=postgresql+psycopg://pospay:pospay@localhost:55432/pospay_test`.
  The main suite connects as a superuser, so row-level security is only exercised by
  `tests/test_postgres/test_rls_*.py` (`rls_client`).
- Test data: `tenant_factory.make(slug=...)` already creates `admin@`, `preparer@`, `viewer@`... at
  `<slug>.example.com`; use other local parts for your own users. Rendered HTML escapes `'` as `&#39;`.
- No linter in the project or CI.

## Conventions that bite
- A new table with `tenant_id` gets an RLS policy in its migration (copy the `_TENANT` pattern in the
  latest `migrations/versions/` file), or a reason in `DELIBERATELY_UNPROTECTED`
  (`tests/test_postgres/test_rls_plugins.py`). Use `batch_alter_table` and `native_enum=False`.
- Under RLS a bank's session sees only its own memberships: any "does this person belong elsewhere?"
  check goes through `db/tenancy.py::memberships_elsewhere`.
- FastAPI matches routes in registration order: literal paths before `/{id}` paths.
- `render_template(request, name, ...)`: never pass a `name=` keyword.
- An audited action: `audit_log_service.record_action(...)` before `db.commit()`. If it's a security
  event, map it in `services/security_events.py` (`AUDIT_EVENTS`, `CATALOG`).
- New permissions go in `auth/permissions.py`; customer-scoped sessions mask
  `CUSTOMER_SCOPE_MASKED_PERMISSIONS`.
- API changes: run `scripts/export_openapi.py` and `scripts/generate_sdks.py`, then
  `tests/test_openapi_contract.py`.
- Dependencies install only from the hashed locks. After changing `pyproject.toml` dependencies,
  run `scripts/lock.sh` and commit `requirements/*.txt` (CI fails on a stale lock). Waive a
  vulnerability only in `.pip-audit-waivers.toml`, with a reason and an expiry.
- A test that proves an ASVS 5.0 requirement gets `@pytest.mark.asvs("6.2.12")`. A requirement's
  status changes in `docs/security/asvs-status.csv`; then `python scripts/asvs_crosswalk.py`
  (a test fails on a stale `docs/compliance/asvs.md` or evidence that doesn't exist).
- A new trust boundary, outside system or data flow: update `threat_model/openclear.py`, then
  `python scripts/render_threat_model.py` (a test fails on a stale page).
- New tables and columns go in the data dictionary (`templates/docs/admin/data-dictionary.html`);
  user-facing changes in `CHANGELOG.md` under "Unreleased".

## Release (version X.Y.Z)
1. Bump `pyproject.toml`; add `"X.Y.Z": "<head revision>"` to `migrations/version_history.py`;
   rename CHANGELOG "Unreleased" to `## X.Y.Z — <date>`; note it in `MASTER_PLAN.md`.
2. Fast-forward `main`, push, wait for CI: `gh run watch <id> --exit-status`.
3. `git tag -a vX.Y.Z` and push it; `gh api repos/chaffed/OpenClear/releases --input <json>`
   (`tag_name`, `name`, `body` from the CHANGELOG section). Publishing the release attaches
   the SBOM (`.github/workflows/release.yml`); check it's there.

## Sandbox deploy (openclear-sandbox on Fly; the only hosted instance)
Pushing a locally built image fails, so use Fly's remote builder from a clean copy:
```
rm -rf ~/.openclear-rel-src && mkdir ~/.openclear-rel-src && git archive <commit> | tar -x -C ~/.openclear-rel-src
cd ~/.openclear-rel-src && { cat Dockerfile; echo; tail -n +12 Dockerfile.sandbox; } > Dockerfile.remote
sed 's/dockerfile = "Dockerfile.sandbox"/dockerfile = "Dockerfile.remote"/' fly.sandbox.toml > fly.remote.toml
fly deploy -c fly.remote.toml --remote-only    # if the network drops mid-build, just run it again
```
Then check `https://openclear-sandbox.fly.dev/health`, the version in `/openapi.json`, and
`scripts/edge_check.py https://openclear-sandbox.fly.dev --expect-ip <your IP>`.

## Live checks against real tools (not in the suite)
`scripts/siem_live_check.py` (Splunk, syslog-ng) · `scripts/sso_live_check.py` (Keycloak, OIDC and
SAML) · `scripts/edge_check.py`. Port 18080 is taken by the Moov ACH container.

## Agent skills

### Issue tracker

GitHub Issues in chaffed/OpenClear, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
