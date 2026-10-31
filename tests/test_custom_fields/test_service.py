# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields (plans/CUSTOM_FIELDS_PLAN.md CF1): definitions and their rules, values by type,
uniqueness, required-in-forms-only, the customer's view, and the provider's fields on banks."""

import pytest
from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import account_service, custom_field_service as cf, customer_service
from openclear.services.custom_field_service import CustomFieldError, FieldInput
from tests.items import _make_customer_with_account


def _field(db_session, tenant, owner="account", **kw):
    data = {"key": "officer_code", "label": "Officer code", "field_type": "text"}
    data.update(kw)
    definition = cf.create_field(db_session, tenant.id, owner, FieldInput(**data), user_id=None)
    db_session.commit()
    return definition


def test_a_definitions_rules(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cf-def")
    for bad in ("Officer", "1st", "a-b", "x" * 41, ""):
        with pytest.raises(CustomFieldError, match="key"):
            _field(db_session, tenant, key=bad)
    with pytest.raises(CustomFieldError, match="one of"):
        _field(db_session, tenant, field_type="string")
    with pytest.raises(CustomFieldError, match="Only a text field"):
        _field(db_session, tenant, key="n", field_type="integer", unique=True)
    with pytest.raises(CustomFieldError, match="at least one option"):
        _field(db_session, tenant, key="branch", field_type="choice")
    first = _field(db_session, tenant, key="officer_code", max_length=10)
    with pytest.raises(CustomFieldError, match="already a field"):
        _field(db_session, tenant, key="officer_code")
    # The key never changes; the label can.
    with pytest.raises(CustomFieldError, match="key can't change"):
        cf.update_field(db_session, tenant.id, first.id, FieldInput(key="officer", label="Officer", field_type="text"), user_id=None)
    cf.update_field(db_session, tenant.id, first.id, FieldInput(key="officer_code", label="Account officer", field_type="text", max_length=20), user_id=None)
    assert first.label == "Account officer" and first.max_length == 20

    # At most three fields in a list, and 25 in all.
    for i in range(3):
        _field(db_session, tenant, key=f"in_list_{i}", show_in_list=True)
    with pytest.raises(CustomFieldError, match="At most 3"):
        _field(db_session, tenant, key="in_list_3", show_in_list=True)
    for i in range(cf.MAX_FIELDS - 4):
        _field(db_session, tenant, key=f"f{i}")
    with pytest.raises(CustomFieldError, match=f"At most {cf.MAX_FIELDS}"):
        _field(db_session, tenant, key="one_too_many")
    cf.archive_field(db_session, tenant.id, first.id)
    db_session.commit()
    assert [f.key for f in cf.list_fields(db_session, tenant.id, "account")][:1] == ["in_list_0"]
    assert any(f.key == "officer_code" for f in cf.list_fields(db_session, tenant.id, "account", include_archived=True))
    _field(db_session, tenant, key="room_again")  # archiving made room


def test_each_type_parses_formats_and_refuses(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cf-types")
    fields = {
        "code": _field(db_session, tenant, key="code", max_length=4),
        "count": _field(db_session, tenant, key="count", field_type="integer"),
        "rate": _field(db_session, tenant, key="rate", field_type="decimal"),
        "opened": _field(db_session, tenant, key="opened", field_type="date"),
        "vip": _field(db_session, tenant, key="vip", field_type="boolean"),
        "branch": _field(db_session, tenant, key="branch", field_type="choice", options=["Main", "North"]),
    }
    changes = cf.set_values(db_session, tenant.id, "account", account.id,
                            {"code": " AB12 ", "count": "-42", "rate": "12.500", "opened": "2026-03-01", "vip": "Yes", "branch": "North"},
                            user_id=users["admin"].id)
    db_session.commit()
    assert cf.values_of(db_session, tenant.id, "account", account.id) == {
        "code": "AB12", "count": "-42", "rate": "12.5", "opened": "2026-03-01", "vip": "true", "branch": "North"}
    assert {c.key: (c.before, c.after) for c in changes.changes}["rate"] == (None, "12.5")
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "custom_field.values.changed")).scalars().one()
    assert entry.metadata_json["after"]["vip"] == "true" and entry.metadata_json["before"]["vip"] is None

    for key, bad, message in (("code", "ABCDE", "at most 4"), ("count", "1.5", "whole number"), ("rate", "x", "a number"),
                              ("rate", "1.1234567", "6 decimal"), ("opened", "03/01/2026", "YYYY-MM-DD"), ("vip", "maybe", "yes or no"),
                              ("branch", "South", "one of"),
                              # What the messages say, exactly (REVIEW_1_33_PLAN.md C7): ASCII digits, YYYY-MM-DD, a plain number.
                              ("count", "\u0663", "whole number"), ("opened", "20260301", "YYYY-MM-DD"), ("rate", "1_000", "a number")):
        with pytest.raises(CustomFieldError, match=message):
            cf.set_values(db_session, tenant.id, "account", account.id, {key: bad}, user_id=None)
    with pytest.raises(CustomFieldError, match="whole number.*YYYY-MM-DD"):  # every field's complaint at once (C10)
        cf.set_values(db_session, tenant.id, "account", account.id, {"count": "1.5", "opened": "nope"}, user_id=None)
    with pytest.raises(CustomFieldError, match="No such field"):
        cf.set_values(db_session, tenant.id, "account", account.id, {"nope": "1"}, user_id=None)

    # Only the keys sent change; None clears; a blank clears too.
    cf.set_values(db_session, tenant.id, "account", account.id, {"count": None, "vip": ""}, user_id=None)
    db_session.commit()
    values = cf.values_of(db_session, tenant.id, "account", account.id)
    assert values["count"] is None and values["vip"] is None and values["code"] == "AB12"
    assert fields["code"].id  # the definitions are untouched by writes


def test_required_is_for_forms_only_and_missing_records_are_listed(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cf-required")
    _field(db_session, tenant, key="officer_code", required=True)
    # A write that doesn't mention the field (the core's sync) goes through.
    cf.set_values(db_session, tenant.id, "account", account.id, {}, user_id=None)
    assert cf.missing_required(db_session, tenant.id, "account") == [account.id]
    # A person's form must fill it in.
    with pytest.raises(CustomFieldError, match="Required: Officer code"):
        cf.set_values(db_session, tenant.id, "account", account.id, {}, user_id=None, require=True)
    cf.set_values(db_session, tenant.id, "account", account.id, {"officer_code": "JS"}, user_id=None, require=True)
    db_session.commit()
    assert cf.missing_required(db_session, tenant.id, "account") == []


def test_unique_text_fields(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cf-unique")
    other = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="9002", name="Other"))
    db_session.commit()
    definition = _field(db_session, tenant, key="agg_id", unique=True)
    cf.set_values(db_session, tenant.id, "account", account.id, {"agg_id": "G-1"}, user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="already on another account"):
        cf.set_values(db_session, tenant.id, "account", other.id, {"agg_id": "G-1"}, user_id=None)
    cf.set_values(db_session, tenant.id, "account", other.id, {"agg_id": "G-2"}, user_id=None)
    db_session.commit()

    # Turning unique on later is refused while duplicates exist, naming them.
    plain = _field(db_session, tenant, key="region")
    cf.set_values(db_session, tenant.id, "account", account.id, {"region": "West"}, user_id=None)
    cf.set_values(db_session, tenant.id, "account", other.id, {"region": "West"}, user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="more than one record: West"):
        cf.update_field(db_session, tenant.id, plain.id, FieldInput(key="region", label="Region", field_type="text", unique=True), user_id=None)
    db_session.rollback()
    cf.set_values(db_session, tenant.id, "account", other.id, {"region": "East"}, user_id=None)
    cf.update_field(db_session, tenant.id, plain.id, FieldInput(key="region", label="Region", field_type="text", unique=True), user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="already on another"):
        cf.set_values(db_session, tenant.id, "account", other.id, {"region": "West"}, user_id=None)
    assert definition.unique


def test_a_fields_type_and_options_once_it_has_values(db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="cf-locked")
    branch = _field(db_session, tenant, key="branch", field_type="choice", options=["Main", "North"])
    cf.set_values(db_session, tenant.id, "account", account.id, {"branch": "North"}, user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="type can't change"):
        cf.update_field(db_session, tenant.id, branch.id, FieldInput(key="branch", label="Branch", field_type="text"), user_id=None)
    # Dropping an option in use retires it: kept on the record, not offered, not accepted anew.
    cf.update_field(db_session, tenant.id, branch.id, FieldInput(key="branch", label="Branch", field_type="choice", options=["Main", "South"]), user_id=None)
    db_session.commit()
    assert [o["value"] for o in cf.active_options(branch)] == ["Main", "South"]
    assert cf.values_of(db_session, tenant.id, "account", account.id)["branch"] == "North"
    cf.set_values(db_session, tenant.id, "account", account.id, {"branch": "North"}, user_id=None)  # unchanged: fine
    with pytest.raises(CustomFieldError, match="one of Main, South"):
        cf.set_values(db_session, tenant.id, "account", account.id, {"branch": "West"}, user_id=None)
    text = _field(db_session, tenant, key="memo", max_length=10)
    cf.set_values(db_session, tenant.id, "account", account.id, {"memo": "short"}, user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="grow, not shrink"):
        cf.update_field(db_session, tenant.id, text.id, FieldInput(key="memo", label="Memo", field_type="text", max_length=5), user_id=None)
    cf.archive_field(db_session, tenant.id, text.id)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="Archived"):
        cf.set_values(db_session, tenant.id, "account", account.id, {"memo": "x"}, user_id=None)
    assert cf.values_of(db_session, tenant.id, "account", account.id)["memo"] == "short"  # still read


def test_restoring_an_archived_field_counts_against_the_limits(db_session, tenant_factory):
    """REVIEW_1_33_PLAN.md C4."""
    tenant, _account, _users = tenant_factory.make(slug="cf-restore")
    shown = [_field(db_session, tenant, key=f"s{n}", show_in_list=True) for n in range(cf.MAX_IN_LIST)]
    extra = _field(db_session, tenant, key="s_extra")
    cf.archive_field(db_session, tenant.id, extra.id)
    cf.archive_field(db_session, tenant.id, shown[0].id)
    db_session.commit()
    assert cf.archive_field(db_session, tenant.id, extra.id, archived=False).archived_at is None  # room in the list: fine
    _field(db_session, tenant, key="s_fourth", show_in_list=True)  # takes the freed place
    with pytest.raises(CustomFieldError, match="show in the list"):
        cf.archive_field(db_session, tenant.id, shown[0].id, archived=False)
    for n in range(cf.MAX_FIELDS - 4):  # four are active: the full complement
        _field(db_session, tenant, key=f"f{n}")
    cf.archive_field(db_session, tenant.id, extra.id)
    db_session.commit()
    _field(db_session, tenant, key="f_last")  # takes the freed place
    with pytest.raises(CustomFieldError, match=f"At most {cf.MAX_FIELDS}"):
        cf.archive_field(db_session, tenant.id, extra.id, archived=False)


def test_a_dropped_option_is_retired_only_while_a_record_holds_it(db_session, tenant_factory):
    """CF-D11 as meant (REVIEW_1_33_PLAN.md C9): a typo'd option can simply go."""
    tenant, account, _users = tenant_factory.make(slug="cf-options")
    branch = _field(db_session, tenant, key="branch", field_type="choice", options=["Main", "North", "Nroth"])
    cf.set_values(db_session, tenant.id, "account", account.id, {"branch": "North"}, user_id=None)
    db_session.commit()
    data = FieldInput(key="branch", label="Branch", field_type="choice", options=["Main"])
    updated = cf.update_field(db_session, tenant.id, branch.id, data, user_id=None)
    assert updated.options == [{"value": "Main", "retired": False}, {"value": "North", "retired": True}]  # Nroth is gone


def test_a_customers_feed_reader_cant_learn_hidden_keys(db_session, tenant_factory):
    """REVIEW_1_33_PLAN.md C3: a field not shown to the customer doesn't exist for its reader."""
    from tests.items import _make_customer_with_account

    tenant, _house, _users = tenant_factory.make(slug="cf-hidden-keys")
    customer, _account = _make_customer_with_account(db_session, tenant, "HK-1")
    _field(db_session, tenant, key="officer_code")
    _field(db_session, tenant, key="tier", shown_to_customer=True)
    assert cf.output_keys(db_session, tenant.id, "officer_code,tier") == ["officer_code", "tier"]
    assert cf.output_keys(db_session, tenant.id, "tier", customer_id=customer.id) == ["tier"]
    with pytest.raises(CustomFieldError, match="No such field: officer_code"):
        cf.output_keys(db_session, tenant.id, "officer_code,tier", customer_id=customer.id)


def test_a_customer_sees_only_its_own_accounts_and_fields_shown_to_it(db_session, tenant_factory):
    tenant, _house, _users = tenant_factory.make(slug="cf-customer")
    mine, my_account = _make_customer_with_account(db_session, tenant, "CF-A")
    _theirs, their_account = _make_customer_with_account(db_session, tenant, "CF-B")
    _field(db_session, tenant, key="officer_code")  # internal
    _field(db_session, tenant, key="agg_id", shown_to_customer=True)
    _field(db_session, tenant, "customer", key="segment", shown_to_customer=True)
    for account in (my_account, their_account):
        cf.set_values(db_session, tenant.id, "account", account.id, {"officer_code": "JS", "agg_id": f"G-{account.account_number}"}, user_id=None)
    cf.set_values(db_session, tenant.id, "customer", mine.id, {"segment": "Retail"}, user_id=None)
    db_session.commit()

    everything = cf.values_for(db_session, tenant.id, "account", [my_account.id, their_account.id])
    assert everything[my_account.id] == {"officer_code": "JS", "agg_id": f"G-{my_account.account_number}"}
    as_customer = cf.values_for(db_session, tenant.id, "account", [my_account.id, their_account.id], customer_id=mine.id)
    assert as_customer[my_account.id] == {"agg_id": f"G-{my_account.account_number}"}  # not the officer code
    assert as_customer[their_account.id] == {}  # not another customer's
    assert cf.values_of(db_session, tenant.id, "customer", mine.id, customer_id=mine.id) == {"segment": "Retail"}


def test_the_providers_fields_on_banks(db_session, tenant_factory):
    bank_a, _a, _u = tenant_factory.make(slug="cf-bank-a")
    bank_b, _b, _v = tenant_factory.make(slug="cf-bank-b")
    erp = cf.create_platform_field(db_session, FieldInput(key="erp_id", label="ERP id", field_type="text", unique=True), user_id=None)
    tier = cf.create_platform_field(db_session, FieldInput(key="tier", label="Tier", field_type="choice", options=["1", "2"], shown_to_bank=True), user_id=None)
    db_session.commit()
    cf.set_platform_values(db_session, bank_a.id, {"erp_id": "ERP-100", "tier": "1"}, user_id=None)
    db_session.commit()
    with pytest.raises(CustomFieldError, match="already on another bank"):
        cf.set_platform_values(db_session, bank_b.id, {"erp_id": "ERP-100"}, user_id=None)
    cf.set_platform_values(db_session, bank_b.id, {"erp_id": "ERP-200"}, user_id=None)
    db_session.commit()
    assert cf.platform_values_for(db_session, [bank_a.id, bank_b.id]) == {bank_a.id: {"erp_id": "ERP-100", "tier": "1"},
                                                                          bank_b.id: {"erp_id": "ERP-200"}}
    # The bank's own pages read only what's shown to it.
    assert cf.platform_values_for(db_session, [bank_a.id], shown_to_bank_only=True) == {bank_a.id: {"tier": "1"}}
    assert erp.unique and tier.shown_to_bank
    with pytest.raises(CustomFieldError, match="already a field"):
        cf.create_platform_field(db_session, FieldInput(key="erp_id", label="Again", field_type="text"), user_id=None)
    from openclear.domain.platform_action_entry import PlatformActionEntry

    assert db_session.execute(select(PlatformActionEntry).where(PlatformActionEntry.action == "platform.custom_field.values.changed")).scalars().first()


def test_values_go_with_the_customer_when_its_deleted(db_session, tenant_factory, monkeypatch):
    """CF-D9: real foreign keys, so customer offboarding's schema walk takes the values."""
    from datetime import datetime, timedelta, timezone

    from openclear.domain.custom_field import CustomFieldValue
    from openclear.services import customer_offboarding_service

    tenant, _house, _users = tenant_factory.make(slug="cf-offboard")
    customer, account = _make_customer_with_account(db_session, tenant, "CF-GO")
    _field(db_session, tenant, key="officer_code")
    _field(db_session, tenant, "customer", key="segment")
    cf.set_values(db_session, tenant.id, "account", account.id, {"officer_code": "JS"}, user_id=None)
    cf.set_values(db_session, tenant.id, "customer", customer.id, {"segment": "Retail"}, user_id=None)
    customer.service_ended_at = datetime.now(timezone.utc) - timedelta(days=91)
    customer.is_active = False
    db_session.commit()
    assert db_session.execute(select(CustomFieldValue).where(CustomFieldValue.tenant_id == tenant.id)).scalars().all()
    customer_id = customer.id
    customer_offboarding_service.sweep(db_session, tenant.id)
    db_session.expire_all()
    assert db_session.execute(select(CustomFieldValue).where(CustomFieldValue.tenant_id == tenant.id)).scalars().all() == []
    assert customer_service.get_customer(db_session, tenant.id, customer_id) is None


def test_a_person_limited_to_some_accounts_sees_only_their_values(db_session, tenant_factory):
    """db/account_scope.py: a value on an account goes with the account; a customer's own value stays."""
    from openclear.db import account_scope

    tenant, _house, _users = tenant_factory.make(slug="cf-scope")
    customer, mine = _make_customer_with_account(db_session, tenant, "CF-S")
    other = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="CF-S-2", name="Other", customer_id=customer.id))
    _field(db_session, tenant, key="agg_id", shown_to_customer=True)
    _field(db_session, tenant, "customer", key="segment", shown_to_customer=True)
    for account in (mine, other):
        cf.set_values(db_session, tenant.id, "account", account.id, {"agg_id": f"G-{account.account_number}"}, user_id=None)
    cf.set_values(db_session, tenant.id, "customer", customer.id, {"segment": "Retail"}, user_id=None)
    db_session.commit()
    account_scope.bind_session_to_accounts(db_session, [mine.id], user_id=None)
    try:
        values = cf.values_for(db_session, tenant.id, "account", [mine.id, other.id], customer_id=customer.id)
        assert values[mine.id] == {"agg_id": f"G-{mine.account_number}"} and values[other.id] == {}
        assert cf.values_of(db_session, tenant.id, "customer", customer.id, customer_id=customer.id) == {"segment": "Retail"}
    finally:
        account_scope.bind_session_to_accounts(db_session, None)
