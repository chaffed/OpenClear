# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields (plans/CUSTOM_FIELDS_PLAN.md): a bank's own fields on its customers and accounts,
and the service provider's on banks. A definition says a field's key, type and rules; a value row
holds one record's value in the column for its type, and a copy in `unique_text` when the field is
unique, so the database enforces uniqueness (NULLs never collide). The provider's tables have no
tenant_id: a bank's session has no row-level policy that could show it a hidden value (CF-D9)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class CustomFieldType(str, enum.Enum):
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    BOOLEAN = "boolean"
    CHOICE = "choice"


class CustomFieldOwner(str, enum.Enum):
    """What a bank's field is on."""

    CUSTOMER = "customer"
    ACCOUNT = "account"


class _Definition:
    """The columns a bank's definition and the provider's share."""

    key: Mapped[str] = mapped_column(String(40), nullable=False)  # [a-z][a-z0-9_]{0,39}; fixed once created
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[str] = mapped_column(String(10), nullable=False)  # CustomFieldType; fixed once values exist
    max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)  # text: at most 255
    # choice: [{"value": "A", "label": "Branch A", "retired": false}, ...]; a retired option stays on records.
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # in forms only (CF-D5)
    unique: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # text only
    help_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class _Value:
    """The columns a bank's value and the provider's share: one typed column holds the value."""

    text_value: Mapped[str | None] = mapped_column(String(255), nullable=True)  # text, and a choice's option value
    integer_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decimal_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    date_value: Mapped[date | None] = mapped_column(Date, nullable=True)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # text_value again while the field is unique, else NULL: the unique constraint is on this.
    unique_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CustomField(_Definition, Base):
    """A bank's field on its customers or its accounts."""

    __tablename__ = "custom_field"
    __table_args__ = (UniqueConstraint("tenant_id", "owner", "key", name="uq_custom_field_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(10), nullable=False)  # CustomFieldOwner
    shown_to_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # read-only, on its accounts
    show_in_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # at most three per list


class CustomFieldValue(_Value, Base):
    """One customer's or account's value for one of its bank's fields."""

    __tablename__ = "custom_field_value"
    __table_args__ = (
        UniqueConstraint("field_id", "customer_id", name="uq_custom_field_value_customer"),
        UniqueConstraint("field_id", "account_id", name="uq_custom_field_value_account"),
        UniqueConstraint("field_id", "unique_text", name="uq_custom_field_value_unique"),
        Index("ix_custom_field_value_text", "field_id", "text_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("custom_field.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id"), nullable=True, index=True)


class PlatformCustomField(_Definition, Base):
    """The service provider's field on banks."""

    __tablename__ = "platform_custom_field"
    __table_args__ = (UniqueConstraint("key", name="uq_platform_custom_field_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    shown_to_bank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # read-only, in its Settings


class PlatformCustomFieldValue(_Value, Base):
    """One bank's value for one of the provider's fields. Keyed bank_id, not tenant_id: no
    row-level policy, not wiped with the bank (it survives offboarding, CF-D9)."""

    __tablename__ = "platform_custom_field_value"
    __table_args__ = (
        UniqueConstraint("field_id", "bank_id", name="uq_platform_custom_field_value_bank"),
        UniqueConstraint("field_id", "unique_text", name="uq_platform_custom_field_value_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform_custom_field.id"), nullable=False, index=True)
    bank_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
