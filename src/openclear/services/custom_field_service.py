# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields (plans/CUSTOM_FIELDS_PLAN.md): a bank's fields on its customers and accounts, the
service provider's on banks, and their values.

Values cross this module's boundary as strings, formatted by type (CF-D8): an ISO date, decimal
text, "true"/"false", a choice's option value. `parse` checks a string against its field and
`format_value` turns a stored value back into one, so forms, the API, files and exports all speak
the same way. A required field is required in forms only (CF-D5): `set_values` takes the keys it's
given and leaves the rest alone, so a write from the core that predates a field can't fail.

Reads by a customer-scoped session go through `values_for(..., customer_id=...)`, the one place
that limits them to the customer's own records and to fields shown to the customer (CF-D3)."""

import re
import uuid
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openclear.domain.account import Account
from openclear.domain.custom_field import (
    CustomField,
    CustomFieldOwner,
    CustomFieldType,
    CustomFieldValue,
    PlatformCustomField,
    PlatformCustomFieldValue,
)
from openclear.domain.customer import Customer

MAX_FIELDS = 25  # per owner (customers, accounts) per bank, and for the provider's
MAX_IN_LIST = 3
MAX_TEXT = 255
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TYPES = {t.value: t for t in CustomFieldType}
OWNERS = {o.value: o for o in CustomFieldOwner}
_TRUE, _FALSE = {"true", "yes", "1", "y"}, {"false", "no", "0", "n"}


class CustomFieldError(ValueError):
    """Fit to show."""


@dataclass(frozen=True)
class FieldInput:
    key: str
    label: str
    field_type: str
    max_length: int | None = None
    options: tuple[str, ...] | list[str] | None = None  # choice: the option values, in order
    required: bool = False
    unique: bool = False
    help_text: str | None = None
    shown_to_customer: bool = False  # bank fields only
    show_in_list: bool = False  # bank fields only
    shown_to_bank: bool = False  # provider fields only


@dataclass
class ValueChange:
    key: str
    before: str | None
    after: str | None


@dataclass
class Changes:
    changes: list[ValueChange] = dataclass_field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def as_metadata(self) -> dict:
        return {"before": {c.key: c.before for c in self.changes}, "after": {c.key: c.after for c in self.changes}}


# --- Values: parsing and formatting -----------------------------------------------------------


def _options(definition) -> list[dict]:
    return list(definition.options or [])


def active_options(definition) -> list[dict]:
    """The options a form offers: not retired."""
    return [o for o in _options(definition) if not o.get("retired")]


def parse(definition, text: str | None) -> Any:
    """A string as the field's type, or None for blank. Raises CustomFieldError, fit to show."""
    text = (text or "").strip() if text is not None else None
    if not text:
        return None
    kind = definition.field_type
    label = definition.label
    if kind == CustomFieldType.TEXT:
        limit = definition.max_length or MAX_TEXT
        if len(text) > limit:
            raise CustomFieldError(f"{label}: at most {limit} characters.")
        return text
    if kind == CustomFieldType.INTEGER:
        if not re.fullmatch(r"[+-]?\d{1,18}", text):
            raise CustomFieldError(f"{label}: a whole number.")
        return int(text)
    if kind == CustomFieldType.DECIMAL:
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise CustomFieldError(f"{label}: a number.") from None
        if not value.is_finite() or value.as_tuple().exponent < -6 or abs(value) >= Decimal("1e12"):
            raise CustomFieldError(f"{label}: a number with at most 6 decimal places and 12 digits before them.")
        return value
    if kind == CustomFieldType.DATE:
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise CustomFieldError(f"{label}: a date as YYYY-MM-DD.") from None
    if kind == CustomFieldType.BOOLEAN:
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise CustomFieldError(f"{label}: yes or no.")
    if kind == CustomFieldType.CHOICE:
        if text not in {o["value"] for o in active_options(definition)}:
            raise CustomFieldError(f"{label}: one of {', '.join(o['value'] for o in active_options(definition)) or 'nothing (no options)'}.")
        return text
    raise CustomFieldError(f"{label}: unknown type {kind!r}.")


def format_value(definition, row) -> str | None:
    """A stored value as the string the API, files and exports carry."""
    if row is None:
        return None
    kind = definition.field_type
    if kind in (CustomFieldType.TEXT, CustomFieldType.CHOICE):
        return row.text_value
    if kind == CustomFieldType.INTEGER:
        return None if row.integer_value is None else str(row.integer_value)
    if kind == CustomFieldType.DECIMAL:
        return None if row.decimal_value is None else format(Decimal(row.decimal_value).normalize(), "f")
    if kind == CustomFieldType.DATE:
        return None if row.date_value is None else row.date_value.isoformat()
    if kind == CustomFieldType.BOOLEAN:
        return None if row.boolean_value is None else ("true" if row.boolean_value else "false")
    return None


def _store(definition, row, value: Any) -> None:
    """Puts a parsed value in its typed column, clearing the others."""
    row.text_value = row.integer_value = row.decimal_value = row.date_value = row.boolean_value = None
    kind = definition.field_type
    if value is None:
        pass
    elif kind in (CustomFieldType.TEXT, CustomFieldType.CHOICE):
        row.text_value = value
    elif kind == CustomFieldType.INTEGER:
        row.integer_value = value
    elif kind == CustomFieldType.DECIMAL:
        row.decimal_value = value
    elif kind == CustomFieldType.DATE:
        row.date_value = value
    elif kind == CustomFieldType.BOOLEAN:
        row.boolean_value = value
    row.unique_text = row.text_value if definition.unique else None


# --- Definitions ------------------------------------------------------------------------------


def _clean_definition(data: FieldInput, *, existing=None, values_exist: bool = False) -> dict:
    """The columns a definition gets from its input, checked. With `existing`, what may change."""
    key = (data.key or "").strip()
    if not KEY.fullmatch(key):
        raise CustomFieldError("A key is 1 to 40 characters: a lowercase letter, then lowercase letters, digits and underscores.")
    if existing is not None and key != existing.key:
        raise CustomFieldError("A field's key can't change once it's created; its label can.")
    label = (data.label or "").strip()
    if not label:
        raise CustomFieldError("Give the field a label.")
    if data.field_type not in TYPES:
        raise CustomFieldError(f"The type is one of: {', '.join(TYPES)}.")
    if existing is not None and data.field_type != existing.field_type and values_exist:
        raise CustomFieldError("A field's type can't change while it has values: archive it and create another.")
    kind = TYPES[data.field_type]
    max_length = None
    if kind == CustomFieldType.TEXT:
        max_length = int(data.max_length) if data.max_length else MAX_TEXT
        if not 1 <= max_length <= MAX_TEXT:
            raise CustomFieldError(f"A text field's maximum length is 1 to {MAX_TEXT}.")
        if existing is not None and values_exist and existing.max_length and max_length < existing.max_length:
            raise CustomFieldError("A text field's maximum length can grow, not shrink, while it has values.")
    options = None
    if kind == CustomFieldType.CHOICE:
        wanted = [str(o).strip() for o in (data.options or []) if str(o).strip()]
        if not wanted:
            raise CustomFieldError("A choice field needs at least one option.")
        if len(wanted) != len(set(wanted)):
            raise CustomFieldError("Each option once.")
        if any(len(o) > MAX_TEXT for o in wanted):
            raise CustomFieldError(f"An option is at most {MAX_TEXT} characters.")
        options = [{"value": o, "retired": False} for o in wanted]
        if existing is not None:
            # An option in use that's no longer wanted is retired, not dropped (CF-D11).
            for old in _options(existing):
                if old["value"] not in wanted:
                    options.append({"value": old["value"], "retired": True})
    if data.unique and kind != CustomFieldType.TEXT:
        raise CustomFieldError("Only a text field can be unique.")
    return {"key": key, "label": label[:255], "field_type": kind.value, "max_length": max_length, "options": options,
            "required": bool(data.required), "unique": bool(data.unique), "help_text": (data.help_text or "").strip()[:500] or None}


def _apply_unique(session: Session, definition, value_model, scope_column) -> None:
    """Turning unique on (or the first time): every value gets its unique_text, which the database
    then keeps unique. Duplicates are refused, naming them (CF-D13)."""
    rows = session.execute(select(value_model).where(value_model.field_id == definition.id)).scalars().all()
    seen: dict[str, int] = {}
    for row in rows:
        if row.text_value is not None:
            seen[row.text_value] = seen.get(row.text_value, 0) + 1
    duplicates = sorted(v for v, n in seen.items() if n > 1)
    if duplicates:
        raise CustomFieldError(f"{definition.label} can't be unique: these values are on more than one record: "
                               f"{', '.join(duplicates[:10])}{'…' if len(duplicates) > 10 else ''}.")
    for row in rows:
        row.unique_text = row.text_value if definition.unique else None
    session.flush()


def check_definition(session: Session, data: FieldInput, *, existing=None) -> dict:
    """Checks a definition's input without saving it: a change that waits for a second person is
    refused now, not at approval. `existing` is the bank's or the provider's definition being changed."""
    if existing is None:
        return _clean_definition(data)
    value_model = CustomFieldValue if isinstance(existing, CustomField) else PlatformCustomFieldValue
    return _clean_definition(data, existing=existing, values_exist=_values_exist(session, value_model, existing.id))


def list_fields(session: Session, tenant_id: uuid.UUID, owner: str, *, include_archived: bool = False) -> list[CustomField]:
    stmt = select(CustomField).where(CustomField.tenant_id == tenant_id, CustomField.owner == owner)
    if not include_archived:
        stmt = stmt.where(CustomField.archived_at.is_(None))
    return list(session.execute(stmt.order_by(CustomField.position, CustomField.created_at)).scalars())


def get_field(session: Session, tenant_id: uuid.UUID, field_id: uuid.UUID) -> CustomField:
    definition = session.get(CustomField, field_id)
    if definition is None or definition.tenant_id != tenant_id:
        raise CustomFieldError("No such field.")
    return definition


def _values_exist(session: Session, value_model, field_id: uuid.UUID) -> bool:
    return session.execute(select(value_model.id).where(value_model.field_id == field_id).limit(1)).first() is not None


def _check_list_limit(session: Session, tenant_id: uuid.UUID, owner: str, *, excluding: uuid.UUID | None) -> None:
    shown = session.execute(select(func.count()).select_from(CustomField).where(
        CustomField.tenant_id == tenant_id, CustomField.owner == owner, CustomField.show_in_list.is_(True),
        CustomField.archived_at.is_(None), CustomField.id != excluding if excluding else True)).scalar_one()
    if shown >= MAX_IN_LIST:
        raise CustomFieldError(f"At most {MAX_IN_LIST} fields show in the list; take one out first.")


def create_field(session: Session, tenant_id: uuid.UUID, owner: str, data: FieldInput, *, user_id: uuid.UUID | None) -> CustomField:
    """Commits nothing; audited by the change that calls it (CF-D11)."""
    if owner not in OWNERS:
        raise CustomFieldError("A field is on customers or on accounts.")
    columns = _clean_definition(data)
    count = session.execute(select(func.count()).select_from(CustomField).where(
        CustomField.tenant_id == tenant_id, CustomField.owner == owner, CustomField.archived_at.is_(None))).scalar_one()
    if count >= MAX_FIELDS:
        raise CustomFieldError(f"At most {MAX_FIELDS} fields on {owner}s; archive one first.")
    if session.execute(select(CustomField.id).where(CustomField.tenant_id == tenant_id, CustomField.owner == owner,
                                                    CustomField.key == columns["key"])).first():
        raise CustomFieldError(f"There's already a field with the key {columns['key']} (archived fields keep theirs).")
    if data.show_in_list:
        _check_list_limit(session, tenant_id, owner, excluding=None)
    definition = CustomField(tenant_id=tenant_id, owner=owner, created_by_user_id=user_id, position=count,
                             shown_to_customer=bool(data.shown_to_customer), show_in_list=bool(data.show_in_list), **columns)
    session.add(definition)
    session.flush()
    return definition


def update_field(session: Session, tenant_id: uuid.UUID, field_id: uuid.UUID, data: FieldInput, *, user_id: uuid.UUID | None) -> CustomField:
    definition = get_field(session, tenant_id, field_id)
    values_exist = _values_exist(session, CustomFieldValue, definition.id)
    columns = _clean_definition(data, existing=definition, values_exist=values_exist)
    if data.show_in_list and not definition.show_in_list:
        _check_list_limit(session, tenant_id, definition.owner, excluding=definition.id)
    turning_unique = columns["unique"] and not definition.unique
    for name, value in columns.items():
        setattr(definition, name, value)
    definition.shown_to_customer, definition.show_in_list = bool(data.shown_to_customer), bool(data.show_in_list)
    session.flush()
    if turning_unique or (not columns["unique"] and values_exist):
        _apply_unique(session, definition, CustomFieldValue, CustomFieldValue.tenant_id)
    return definition


def archive_field(session: Session, tenant_id: uuid.UUID, field_id: uuid.UUID, *, archived: bool = True) -> CustomField:
    """Archived: hidden from forms, still read everywhere, never written (CF-D11)."""
    definition = get_field(session, tenant_id, field_id)
    definition.archived_at = datetime.now(timezone.utc) if archived else None
    session.flush()
    return definition


# --- Values -----------------------------------------------------------------------------------


def _record_column(owner: str):
    return CustomFieldValue.customer_id if owner == CustomFieldOwner.CUSTOMER else CustomFieldValue.account_id


def _record(session: Session, tenant_id: uuid.UUID, owner: str, record_id: uuid.UUID):
    model = Customer if owner == CustomFieldOwner.CUSTOMER else Account
    record = session.get(model, record_id)
    if record is None or record.tenant_id != tenant_id:
        raise CustomFieldError(f"No such {owner}.")
    return record


def values_for(session: Session, tenant_id: uuid.UUID, owner: str, record_ids, *, customer_id: uuid.UUID | None = None,
               include_archived: bool = True) -> dict[uuid.UUID, dict[str, str | None]]:
    """Each record's values, by field key, as strings. With `customer_id` (a customer-scoped
    session), only that customer's own records, and only fields shown to the customer (CF-D3)."""
    ids = list(record_ids)
    if not ids:
        return {}
    fields = list_fields(session, tenant_id, owner, include_archived=include_archived)
    if customer_id is not None:
        fields = [f for f in fields if f.shown_to_customer]
    if not fields:
        return {i: {} for i in ids}
    by_id = {f.id: f for f in fields}
    stmt = select(CustomFieldValue).where(CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.field_id.in_(by_id),
                                          _record_column(owner).in_(ids))
    if customer_id is not None:
        if owner == CustomFieldOwner.CUSTOMER:
            stmt = stmt.where(CustomFieldValue.customer_id == customer_id)
        else:
            stmt = stmt.join(Account, Account.id == CustomFieldValue.account_id).where(Account.customer_id == customer_id)
    result: dict[uuid.UUID, dict[str, str | None]] = {i: {} for i in ids}
    for row in session.execute(stmt).scalars():
        record_id = row.customer_id if owner == CustomFieldOwner.CUSTOMER else row.account_id
        result[record_id][by_id[row.field_id].key] = format_value(by_id[row.field_id], row)
    return result


def values_of(session: Session, tenant_id: uuid.UUID, owner: str, record_id: uuid.UUID, **kw) -> dict[str, str | None]:
    return values_for(session, tenant_id, owner, [record_id], **kw).get(record_id, {})


def set_values(session: Session, tenant_id: uuid.UUID, owner: str, record_id: uuid.UUID, values: dict[str, str | None], *,
               user_id: uuid.UUID | None, channel: str = "web", require: bool = False) -> Changes:
    """Sets the values named in `values` (a key to a string, or None to clear) and leaves every
    other field as it is (CF-D5). With `require` (a person's form), every required active field
    must end up with a value. Audited as custom_field.values.changed with before and after
    (CF-D10). Commits nothing."""
    from openclear.services import audit_log_service

    record = _record(session, tenant_id, owner, record_id)
    fields = {f.key: f for f in list_fields(session, tenant_id, owner, include_archived=True)}
    unknown = sorted(k for k in values if k not in fields)
    if unknown:
        raise CustomFieldError(f"No such field: {', '.join(unknown)}.")
    archived = sorted(k for k in values if fields[k].archived_at is not None)
    if archived:
        raise CustomFieldError(f"Archived, so not written: {', '.join(archived)}.")
    existing = {row.field_id: row for row in session.execute(select(CustomFieldValue).where(
        CustomFieldValue.tenant_id == tenant_id, _record_column(owner) == record_id)).scalars()}
    changes = Changes()
    for key, text in values.items():
        definition = fields[key]
        row = existing.get(definition.id)
        before = format_value(definition, row)
        # A retired option already on the record stays if it's sent back unchanged.
        if definition.field_type == CustomFieldType.CHOICE and text is not None and text.strip() == before:
            continue
        parsed = parse(definition, text)
        after = None if parsed is None else format_value(definition, _preview(definition, parsed))
        if before == after:
            continue
        if row is None:
            row = CustomFieldValue(tenant_id=tenant_id, field_id=definition.id,
                                   customer_id=record_id if owner == CustomFieldOwner.CUSTOMER else None,
                                   account_id=record_id if owner == CustomFieldOwner.ACCOUNT else None)
            session.add(row)
            existing[definition.id] = row
        _store(definition, row, parsed)
        row.updated_by_user_id = user_id
        changes.changes.append(ValueChange(key, before, after))
    if require:
        missing = [f.label for f in fields.values() if f.required and f.archived_at is None
                   and format_value(f, existing.get(f.id)) is None]
        if missing:
            raise CustomFieldError(f"Required: {', '.join(missing)}.")
    if not changes:
        return changes
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        clashing = [c for c in changes.changes if fields[c.key].unique]
        raise CustomFieldError(f"{', '.join(fields[c.key].label for c in clashing) or 'A unique value'} is already on another "
                               f"{owner}; each must be different.") from exc
    audit_log_service.record_action(
        session, tenant_id, actor_user_id=user_id, channel=channel, action="custom_field.values.changed",
        summary=f"Custom fields changed on {owner} {getattr(record, 'customer_number', None) or getattr(record, 'account_number', '')}: "
                + ", ".join(c.key for c in changes.changes), resource_type=owner, resource_id=record_id,
        metadata=changes.as_metadata())
    return changes


class _preview:
    """A throwaway row to format a parsed value through the same code as a stored one."""

    def __init__(self, definition, value) -> None:
        _store(definition, self, value)


def missing_required(session: Session, tenant_id: uuid.UUID, owner: str) -> list[uuid.UUID]:
    """The records with no value for a required, active field (for the lists' filter and the
    control check, CF-D5)."""
    fields = [f for f in list_fields(session, tenant_id, owner) if f.required]
    if not fields:
        return []
    model = Customer if owner == CustomFieldOwner.CUSTOMER else Account
    ids = list(session.execute(select(model.id).where(model.tenant_id == tenant_id)).scalars())
    have = values_for(session, tenant_id, owner, ids, include_archived=False)
    return [i for i in ids if any(have[i].get(f.key) is None for f in fields)]


# --- The provider's fields on banks -------------------------------------------------------------


def list_platform_fields(session: Session, *, include_archived: bool = False) -> list[PlatformCustomField]:
    stmt = select(PlatformCustomField)
    if not include_archived:
        stmt = stmt.where(PlatformCustomField.archived_at.is_(None))
    return list(session.execute(stmt.order_by(PlatformCustomField.position, PlatformCustomField.created_at)).scalars())


def create_platform_field(session: Session, data: FieldInput, *, user_id: uuid.UUID | None) -> PlatformCustomField:
    columns = _clean_definition(data)
    count = session.execute(select(func.count()).select_from(PlatformCustomField).where(PlatformCustomField.archived_at.is_(None))).scalar_one()
    if count >= MAX_FIELDS:
        raise CustomFieldError(f"At most {MAX_FIELDS} fields on banks; archive one first.")
    if session.execute(select(PlatformCustomField.id).where(PlatformCustomField.key == columns["key"])).first():
        raise CustomFieldError(f"There's already a field with the key {columns['key']} (archived fields keep theirs).")
    definition = PlatformCustomField(created_by_user_id=user_id, position=count, shown_to_bank=bool(data.shown_to_bank), **columns)
    session.add(definition)
    session.flush()
    return definition


def update_platform_field(session: Session, field_id: uuid.UUID, data: FieldInput, *, user_id: uuid.UUID | None) -> PlatformCustomField:
    definition = session.get(PlatformCustomField, field_id)
    if definition is None:
        raise CustomFieldError("No such field.")
    values_exist = _values_exist(session, PlatformCustomFieldValue, definition.id)
    columns = _clean_definition(data, existing=definition, values_exist=values_exist)
    turning_unique = columns["unique"] and not definition.unique
    for name, value in columns.items():
        setattr(definition, name, value)
    definition.shown_to_bank = bool(data.shown_to_bank)
    session.flush()
    if turning_unique or (not columns["unique"] and values_exist):
        _apply_unique(session, definition, PlatformCustomFieldValue, PlatformCustomFieldValue.bank_id)
    return definition


def archive_platform_field(session: Session, field_id: uuid.UUID, *, archived: bool = True) -> PlatformCustomField:
    definition = session.get(PlatformCustomField, field_id)
    if definition is None:
        raise CustomFieldError("No such field.")
    definition.archived_at = datetime.now(timezone.utc) if archived else None
    session.flush()
    return definition


def platform_values_for(session: Session, bank_ids, *, shown_to_bank_only: bool = False,
                        include_archived: bool = True) -> dict[uuid.UUID, dict[str, str | None]]:
    """Each bank's values by key. With `shown_to_bank_only`, what the bank itself may see (CF-D2):
    the only way a bank's pages ever read these."""
    ids = list(bank_ids)
    if not ids:
        return {}
    fields = list_platform_fields(session, include_archived=include_archived)
    if shown_to_bank_only:
        fields = [f for f in fields if f.shown_to_bank]
    by_id = {f.id: f for f in fields}
    result: dict[uuid.UUID, dict[str, str | None]] = {i: {} for i in ids}
    if not by_id:
        return result
    for row in session.execute(select(PlatformCustomFieldValue).where(PlatformCustomFieldValue.field_id.in_(by_id),
                                                                      PlatformCustomFieldValue.bank_id.in_(ids))).scalars():
        result[row.bank_id][by_id[row.field_id].key] = format_value(by_id[row.field_id], row)
    return result


def set_platform_values(session: Session, bank_id: uuid.UUID, values: dict[str, str | None], *, user_id: uuid.UUID | None) -> Changes:
    """As set_values, for a bank's values of the provider's fields; in the provider's log."""
    from openclear.domain.tenant import Tenant
    from openclear.services import platform_log_service

    bank = session.get(Tenant, bank_id)
    if bank is None:
        raise CustomFieldError("No such bank.")
    fields = {f.key: f for f in list_platform_fields(session, include_archived=True)}
    unknown = sorted(k for k in values if k not in fields)
    if unknown:
        raise CustomFieldError(f"No such field: {', '.join(unknown)}.")
    archived = sorted(k for k in values if fields[k].archived_at is not None)
    if archived:
        raise CustomFieldError(f"Archived, so not written: {', '.join(archived)}.")
    existing = {row.field_id: row for row in session.execute(select(PlatformCustomFieldValue).where(
        PlatformCustomFieldValue.bank_id == bank_id)).scalars()}
    changes = Changes()
    for key, text in values.items():
        definition = fields[key]
        row = existing.get(definition.id)
        before = format_value(definition, row)
        if definition.field_type == CustomFieldType.CHOICE and text is not None and text.strip() == before:
            continue
        parsed = parse(definition, text)
        after = None if parsed is None else format_value(definition, _preview(definition, parsed))
        if before == after:
            continue
        if row is None:
            row = PlatformCustomFieldValue(field_id=definition.id, bank_id=bank_id)
            session.add(row)
            existing[definition.id] = row
        _store(definition, row, parsed)
        row.updated_by_user_id = user_id
        changes.changes.append(ValueChange(key, before, after))
    if not changes:
        return changes
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        clashing = [c for c in changes.changes if fields[c.key].unique]
        raise CustomFieldError(f"{', '.join(fields[c.key].label for c in clashing) or 'A unique value'} is already on another bank; "
                               "each must be different.") from exc
    platform_log_service.record(session, actor_user_id=user_id, action="platform.custom_field.values.changed",
                                summary=f"Custom fields changed on {bank.name}: {', '.join(c.key for c in changes.changes)}"[:500],
                                resource_type="tenant", resource_id=bank_id, metadata=changes.as_metadata())
    return changes
