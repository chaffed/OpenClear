# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Issue-file layouts (UX_PLAN.md U1; domain/import_layout.py, bulk_import/layouts.py): who
can see and change which, the default for a customer, and the copy an upload keeps."""

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from openclear.bulk_import import layouts
from openclear.domain.import_layout import ImportLayout

KIND = "issued_items"


class LayoutNotFound(LookupError):
    pass


def visible(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list[ImportLayout]:
    """What a person can use: a customer's own and the bank's; bank staff, every one."""
    stmt = select(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == KIND)
    if customer_id is not None:
        stmt = stmt.where(or_(ImportLayout.customer_id == customer_id, ImportLayout.customer_id.is_(None)))
    return list(session.execute(stmt.order_by(ImportLayout.customer_id.is_not(None), ImportLayout.name)).scalars())


def get(session: Session, tenant_id: uuid.UUID, layout_id: uuid.UUID, customer_id: uuid.UUID | None) -> ImportLayout:
    layout = session.get(ImportLayout, layout_id)
    if layout is None or layout.tenant_id != tenant_id or (customer_id is not None and layout.customer_id not in (None, customer_id)):
        raise LayoutNotFound()
    return layout


def editable(layout: ImportLayout, customer_id: uuid.UUID | None) -> bool:
    """A customer's people change their own layouts; the bank's staff change any."""
    return customer_id is None or layout.customer_id == customer_id


def default_for(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> ImportLayout | None:
    """The customer's default, else the bank's."""
    for owner in ([customer_id] if customer_id is not None else []) + [None]:
        stmt = select(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == KIND, ImportLayout.is_default.is_(True))
        stmt = stmt.where(ImportLayout.customer_id.is_(None) if owner is None else ImportLayout.customer_id == owner)
        found = session.execute(stmt.limit(1)).scalar_one_or_none()
        if found is not None:
            return found
    return None


def save(session: Session, tenant_id: uuid.UUID, *, layout: ImportLayout | None, name: str, owner_customer_id: uuid.UUID | None,
         raw_settings: dict, is_default: bool, user_id: uuid.UUID | None) -> ImportLayout:
    """Creates or changes a layout. Raises layouts.LayoutError."""
    name = (name or "").strip()
    if not name:
        raise layouts.LayoutError("Give the layout a name.")
    if len(name) > 100:
        raise layouts.LayoutError("The name must be 100 characters or fewer.")
    settings = layouts.clean_settings(raw_settings)
    if layout is None:
        layout = ImportLayout(tenant_id=tenant_id, kind=KIND, created_by_user_id=user_id)
        session.add(layout)
    layout.name, layout.customer_id, layout.settings, layout.is_default = name, owner_customer_id, settings, is_default
    session.flush()
    if is_default:
        owner = ImportLayout.customer_id.is_(None) if owner_customer_id is None else ImportLayout.customer_id == owner_customer_id
        session.execute(update(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == KIND, owner,
                                                   ImportLayout.id != layout.id).values(is_default=False))
    return layout


def delete(session: Session, layout: ImportLayout) -> None:
    session.delete(layout)
    session.flush()


def upload_options(layout: ImportLayout) -> dict:
    """What an upload keeps: which layout, and a copy of its settings, so editing the layout
    later never changes what an earlier upload meant."""
    return {"layout": {"id": str(layout.id), "name": layout.name, "settings": layout.settings}}
