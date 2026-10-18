# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Breached and guessable passwords (OWASP_PLAN.md OW-D15–OW-D17): the bundled list, a list
in HIBP's download format, HIBP's range API, context words, the 15-character default for new
banks, and passwords re-checked at sign-in."""

import hashlib

import httpx
import pytest

from openclear.auth import breached_passwords
from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.auth.password_policy import PasswordPolicy, context_words, validate_password
from openclear.auth.security import hash_password
from openclear.config import get_settings
from openclear.domain.security_event import SecurityEvent
from openclear.domain.tenant import Tenant
from openclear.services import user_service
from tests.conftest import TenantFactory

POLICY = PasswordPolicy(min_length=8, require_uppercase=False, require_lowercase=False, require_number=False,
                        require_symbol=False)


def _sha1(password: str) -> str:
    return hashlib.sha1(password.encode()).hexdigest().upper()


@pytest.mark.asvs("6.2.4")
def test_the_bundled_list_knows_common_passwords():
    assert breached_passwords.is_breached("password")
    assert breached_passwords.is_breached("123456")
    assert not breached_passwords.is_breached("correct horse battery staple x9!")


def test_a_list_in_hibp_download_format_is_searched_in_place(tmp_path, monkeypatch):
    extra = ["uncommon-but-leaked-1", "uncommon-but-leaked-2", "uncommon-but-leaked-3"]
    lines = sorted(f"{_sha1(p)}:{i + 1}" for i, p in enumerate(extra))
    lines += sorted(f"{_sha1(f'filler-{i}')}:1" for i in range(500))
    path = tmp_path / "pwned.txt"
    path.write_text("\r\n".join(sorted(lines)) + "\r\n")
    monkeypatch.setattr(get_settings(), "breached_password_file", str(path))

    for password in extra + ["filler-0", "filler-499"]:
        assert breached_passwords.is_breached(password), password
    assert not breached_passwords.is_breached("never-leaked-anywhere-7!")


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return self._handler(url)


@pytest.mark.asvs("6.2.12")
def test_hibp_range_api_sends_only_a_prefix(monkeypatch):
    password = "uncommon-but-in-hibp-42"
    seen = []

    def handler(url):
        seen.append(url)
        suffix = _sha1(password)[5:]
        return httpx.Response(200, text=f"0000000000000000000000000000000000A:0\r\n{suffix}:7\r\n",
                              request=httpx.Request("GET", url))

    from openclear.auth import outbound_http

    monkeypatch.setattr(outbound_http, "public_only_client", lambda **kw: _FakeClient(handler))
    monkeypatch.setattr(get_settings(), "breached_password_hibp", True)
    assert breached_passwords.is_breached(password)
    assert seen == ["https://api.pwnedpasswords.com/range/" + _sha1(password)[:5]]


def test_hibp_outage_falls_back_to_the_lists(monkeypatch, caplog):
    def handler(url):
        raise httpx.ConnectTimeout("down")

    from openclear.auth import outbound_http

    monkeypatch.setattr(outbound_http, "public_only_client", lambda **kw: _FakeClient(handler))
    monkeypatch.setattr(get_settings(), "breached_password_hibp", True)
    assert not breached_passwords.is_breached("uncommon-but-in-hibp-42")
    assert breached_passwords.is_breached("password")  # the bundled list still decides
    assert "HIBP unavailable" in caplog.text


@pytest.mark.asvs("6.2.11")
def test_context_words_refuse_names_and_email():
    tenant = Tenant(name="Acme Savings Bank", slug="acme")
    words = context_words(email="pat.lee@example.com", tenants=[tenant])
    assert "openclear" in words and "acmesavingsbank" in words and "patlee" in words
    for password in ("Acme-Savings-Bank-2026!", "my-pat.lee-password", "OpenClear rocks 99"):
        with pytest.raises(ValueError, match="name of your organization"):
            validate_password(password, POLICY, context=words, check_breached=False)
    validate_password("a different phrase entirely", POLICY, context=words, check_breached=False)


def test_a_breached_password_is_refused_with_the_policy_reasons_first():
    with pytest.raises(ValueError, match="appeared in a data breach"):
        validate_password("Password1", POLICY)
    with pytest.raises(ValueError) as exc:
        validate_password("short", POLICY)
    assert "at least 8" in str(exc.value) and "breach" not in str(exc.value)


@pytest.mark.asvs("6.2.1")
def test_new_banks_require_15_characters(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="pw-default-15")
    assert tenant.password_min_length == 15


def _set_password(db_session, user, password):
    user.hashed_password = hash_password(password)
    db_session.commit()


def _weak_events(db_session, user):
    return db_session.query(SecurityEvent).filter(
        SecurityEvent.name == "auth.password.weak", SecurityEvent.actor_id == str(user.id)
    ).all()


@pytest.mark.asvs("6.2.12")
def test_signing_in_with_a_breached_password_forces_a_change(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-weak-breached")
    _set_password(db_session, users["admin"], "1q2w3e4r5t6y7u8i9o0p")  # long enough, but breached

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, "1q2w3e4r5t6y7u8i9o0p")

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert users["admin"].must_change_password is True
    assert [e.reason for e in _weak_events(db_session, users["admin"])] == ["breached"]


def test_signing_in_with_a_password_shorter_than_today_s_policy_forces_a_change(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-weak-short")
    tenant.password_min_length = 20  # TenantFactory.PASSWORD is 17 characters
    db_session.commit()

    authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert users["admin"].must_change_password is True
    assert [e.reason for e in _weak_events(db_session, users["admin"])] == ["too_short"]


def test_a_good_password_signs_in_without_a_forced_change(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-weak-none")
    authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)
    assert users["admin"].must_change_password is False
    assert _weak_events(db_session, users["admin"]) == []


def test_changing_to_a_breached_or_context_password_is_refused(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="pw-change-refused")
    admin = users["admin"]
    for new in ("1q2w3e4r5t6y7u8i9o0p", f"{tenant.slug}-is-my-password"):
        with pytest.raises(user_service.PasswordChangeError):
            user_service.change_own_password(db_session, tenant.id, admin.id, current_password=TenantFactory.PASSWORD,
                                             new_password=new)


# --- Account enumeration (ASVS 6.3.8) ---


@pytest.mark.asvs("6.3.8")
def test_unknown_and_locked_accounts_spend_a_password_check(db_session, tenant_factory, monkeypatch):
    from openclear.auth import login_service

    tenant, _account, users = tenant_factory.make(slug="pw-enum")
    calls = []
    monkeypatch.setattr(login_service, "verify_password", lambda pw, hashed: calls.append(pw) or False)

    authenticate_password(db_session, tenant.slug, "nobody@pw-enum.example.com", "guess-1")
    authenticate_password(db_session, "no-such-bank", users["admin"].email, "guess-2")
    from datetime import datetime, timedelta, timezone

    users["admin"].locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    db_session.commit()
    result = authenticate_password(db_session, tenant.slug, users["admin"].email, "guess-3")

    assert result.outcome == PasswordLoginOutcome.LOCKED
    assert calls == ["guess-1", "guess-2", "guess-3"]


@pytest.mark.asvs("6.3.8")
def test_the_api_answers_a_locked_account_like_a_wrong_password(client, db_session, tenant_factory):
    from datetime import datetime, timedelta, timezone

    tenant, _account, users = tenant_factory.make(slug="pw-enum-api")
    users["admin"].locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    db_session.commit()
    locked = client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": users["admin"].email,
                                                    "password": TenantFactory.PASSWORD})
    unknown = client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": "nobody@pw-enum-api.example.com",
                                                     "password": TenantFactory.PASSWORD})
    assert locked.status_code == unknown.status_code == 401
    assert locked.json() == unknown.json()


# --- bcrypt's 72-byte limit (ASVS 6.2.8, 6.2.9) ---


@pytest.mark.asvs("6.2.8")
def test_every_character_of_a_long_password_counts():
    from openclear.auth.security import verify_password

    long = "correct horse battery staple " * 4  # 116 characters
    hashed = hash_password(long)
    assert verify_password(long, hashed)
    assert not verify_password(long[:-1] + "X", hashed)  # past bcrypt's 72 bytes, still different
    multibyte = "密码" * 32  # 64 characters, 192 bytes
    assert not verify_password("密码" * 31 + "密", hash_password(multibyte))


def test_an_old_hash_still_works_and_is_upgraded_at_sign_in(db_session, tenant_factory):
    import bcrypt

    from openclear.auth.security import password_needs_rehash

    tenant, _account, users = tenant_factory.make(slug="pw-rehash")
    admin = users["admin"]
    admin.hashed_password = bcrypt.hashpw(TenantFactory.PASSWORD.encode(), bcrypt.gensalt()).decode()  # pre-1.22.1
    db_session.commit()
    assert password_needs_rehash(admin.hashed_password)

    result = authenticate_password(db_session, tenant.slug, admin.email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert admin.hashed_password.startswith("ocv2:")
    assert authenticate_password(db_session, tenant.slug, admin.email, TenantFactory.PASSWORD).outcome == PasswordLoginOutcome.SUCCESS
