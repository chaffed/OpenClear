# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Row-level security covers every bank's table, including the plugin choices a bank makes
(PLUGIN_PLAN.md PL2), apart from the few left out on purpose."""

import pytest
from sqlalchemy import text

from openclear.services import plugin_service
from tests.conftest import TEST_DATABASE_URL, login_headers
from tests.test_auth.test_plugins_bank_choices import _line, _upload, example_plugin  # noqa: F401 -- fixture

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


# Each left out on purpose, with the reason in the migration named.
DELIBERATELY_UNPROTECTED = {
    "decision": "the shared model trains on every bank's decisions (6b9b23b81e31)",
    "exception_item": "the same (6b9b23b81e31)",
    "ml_model": "the same: the shared model's rows belong to no bank (6b9b23b81e31)",
    "job": "workers claim every bank's jobs, then bind to the job's bank (c4f6a8b0d2e5)",
    "notification": "tenant_id is nullable, like user's (c3d4e5f6a7b8)",
    "security_event": "some events belong to no bank, and delivery reads every bank's (b9d1f3a5c7e0)",
}


def test_every_table_with_a_tenant_id_is_protected(db_session):
    """A new bank table gets row-level security unless it's added above, with a reason."""
    unprotected = db_session.execute(text("""
        SELECT c.relname FROM pg_class c
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = current_schema()
        WHERE c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
        ORDER BY 1
    """)).scalars().all()
    assert set(unprotected) == set(DELIBERATELY_UNPROTECTED)


def test_a_banks_plugin_choices_work_under_rls(example_plugin, rls_client, db_session, tenant_factory):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="rls-plug-on")
    other, _other_account, other_users = tenant_factory.make(slug="rls-plug-off")
    plugin_service.set_enabled(db_session, tenant.id, "import_formats", "paid_items/example_fixed_width", True, user_id=None)
    db_session.commit()

    content = _line(account.account_number, "5001", 100) + "\n"
    accepted = _upload(rls_client, login_headers(rls_client, tenant.slug, users["admin"].email), content)
    assert accepted.status_code == 202, accepted.text
    assert _upload(rls_client, login_headers(rls_client, other.slug, other_users["admin"].email), content).status_code == 422
