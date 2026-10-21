# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Forms that work on a phone and with a password manager (UI_PLAN.md UI6): amounts and check
and account numbers bring up a number keyboard, and sign-in says what each field is for."""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "openclear" / "templates"
NUMBERS = re.compile(r'name="(amount|amount_min|amount_max|max_amount|presented_amount|issued_amount|check_number|account_number)"')


def test_every_number_field_brings_up_a_number_keyboard():
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        for tag in re.findall(r"<input\b[^>]*>", path.read_text()):
            if NUMBERS.search(tag) and "inputmode=" not in tag and not re.search(r'type="(hidden|checkbox|radio)"', tag):
                missing.append(f"{path.relative_to(TEMPLATES)}: {tag[:80]}")
    assert missing == [], "Add inputmode=\"decimal\" (amounts) or \"numeric\" (check and account numbers):\n" + "\n".join(missing)


def test_sign_in_fields_say_what_they_are(client, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="forms-login")
    generic = client.get("/ui/login").text
    assert 'autocomplete="username"' in generic and 'autocomplete="current-password"' in generic
    assert "Organization slug" not in generic and "short name in your organization" in generic
    assert "Forgotten your password?" in generic
    branded = client.get(f"/ui/login/{tenant.slug}").text
    assert f'<input type="hidden" name="tenant_slug" value="{tenant.slug}">' in branded
