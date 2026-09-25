# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.auth.password_policy import PasswordPolicy, describe, effective_policy, validate_password


def _tenant(**overrides):
    class _T:
        password_min_length = overrides.get("password_min_length", 8)
        password_require_uppercase = overrides.get("password_require_uppercase", False)
        password_require_lowercase = overrides.get("password_require_lowercase", False)
        password_require_number = overrides.get("password_require_number", False)
        password_require_symbol = overrides.get("password_require_symbol", False)

    return _T()


def _customer(**overrides):
    class _C:
        password_min_length = overrides.get("password_min_length", None)
        password_require_uppercase = overrides.get("password_require_uppercase", False)
        password_require_lowercase = overrides.get("password_require_lowercase", False)
        password_require_number = overrides.get("password_require_number", False)
        password_require_symbol = overrides.get("password_require_symbol", False)

    return _C()


def test_effective_policy_with_no_customer_is_just_the_tenants():
    tenant = _tenant(password_min_length=10, password_require_number=True)
    policy = effective_policy(tenant, None)
    assert policy == PasswordPolicy(min_length=10, require_uppercase=False, require_lowercase=False, require_number=True, require_symbol=False)


def test_effective_policy_customer_can_only_strengthen_never_weaken():
    tenant = _tenant(password_min_length=10, password_require_number=True)
    # Customer's own stored flags are all "off" and its min_length is None -- effective
    # policy must be identical to the tenant's, never weaker.
    customer = _customer()
    policy = effective_policy(tenant, customer)
    assert policy.min_length == 10
    assert policy.require_number is True


def test_effective_policy_customer_min_length_takes_the_max():
    tenant = _tenant(password_min_length=10)
    stricter_customer = _customer(password_min_length=16)
    looser_customer = _customer(password_min_length=4)  # can't actually be stored via
    # customer_service.set_password_policy (which rejects it), but effective_policy
    # itself must still never go below the tenant's even if it somehow got stored.
    assert effective_policy(tenant, stricter_customer).min_length == 16
    assert effective_policy(tenant, looser_customer).min_length == 10


def test_effective_policy_boolean_flags_or_combine():
    tenant = _tenant(password_require_uppercase=True)
    customer = _customer(password_require_symbol=True)
    policy = effective_policy(tenant, customer)
    assert policy.require_uppercase is True  # from the tenant
    assert policy.require_symbol is True  # from the customer
    assert policy.require_number is False  # neither requires it


def test_validate_password_accepts_compliant_password():
    policy = PasswordPolicy(min_length=8, require_uppercase=True, require_lowercase=True, require_number=True, require_symbol=True)
    validate_password("Str0ng!Pass", policy)  # must not raise


def test_validate_password_rejects_and_lists_every_unmet_requirement():
    policy = PasswordPolicy(min_length=12, require_uppercase=True, require_lowercase=True, require_number=True, require_symbol=True)
    with pytest.raises(ValueError) as exc_info:
        validate_password("short", policy)
    message = str(exc_info.value)
    assert "at least 12 characters" in message
    assert "uppercase" in message
    assert "number" in message
    assert "symbol" in message


def test_validate_password_permissive_policy_accepts_simple_password():
    policy = PasswordPolicy(min_length=8, require_uppercase=False, require_lowercase=False, require_number=False, require_symbol=False)
    validate_password("test-password-123", policy)  # must not raise -- matches TenantFactory.PASSWORD


def test_describe_lists_requirements_in_plain_english():
    policy = PasswordPolicy(min_length=8, require_uppercase=True, require_lowercase=False, require_number=True, require_symbol=False)
    assert describe(policy) == "at least 8 characters, an uppercase letter, and a number"


def test_describe_length_only():
    policy = PasswordPolicy(min_length=8, require_uppercase=False, require_lowercase=False, require_number=False, require_symbol=False)
    assert describe(policy) == "at least 8 characters"
