# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""File layouts (UX_PLAN.md U1, PAID_ITEM_LAYOUTS_PLAN.md; domain/import_layout.py,
bulk_import/layouts.py): who can see and change which, the default, and the copy an upload
keeps. A layout reads issued checks (a customer's or the bank's) or posted checks (the bank's
core file, PIL-D2: the bank's alone, never a customer's)."""

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from openclear.bulk_import import layouts
from openclear.domain.import_layout import ImportLayout

KINDS = (layouts.ISSUED_ITEMS, layouts.PAID_ITEMS)
# Who builds and uses each kind of layout.
PERMISSION = {layouts.ISSUED_ITEMS: "issued_item:write", layouts.PAID_ITEMS: "paid_item:write"}
NOUN = {layouts.ISSUED_ITEMS: "issue-file layout", layouts.PAID_ITEMS: "posted-check layout"}


class LayoutNotFound(LookupError):
    pass


def visible(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, kind: str = layouts.ISSUED_ITEMS) -> list[ImportLayout]:
    """What a person can use: a customer's own and the bank's issued-check layouts; bank
    staff, every one. Posted-check layouts are the bank's staff's only."""
    if kind == layouts.PAID_ITEMS and customer_id is not None:
        return []
    stmt = select(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == kind)
    if customer_id is not None:
        stmt = stmt.where(or_(ImportLayout.customer_id == customer_id, ImportLayout.customer_id.is_(None)))
    return list(session.execute(stmt.order_by(ImportLayout.customer_id.is_not(None), ImportLayout.name)).scalars())


def get(session: Session, tenant_id: uuid.UUID, layout_id: uuid.UUID, customer_id: uuid.UUID | None,
        kind: str | None = None) -> ImportLayout:
    """One the caller can use, of `kind` when given."""
    layout = session.get(ImportLayout, layout_id)
    if layout is None or layout.tenant_id != tenant_id or (customer_id is not None and layout.customer_id not in (None, customer_id)):
        raise LayoutNotFound()
    if (kind is not None and layout.kind != kind) or (layout.kind == layouts.PAID_ITEMS and customer_id is not None):
        raise LayoutNotFound()
    return layout


def editable(layout: ImportLayout, customer_id: uuid.UUID | None) -> bool:
    """A customer's people change their own layouts; the bank's staff change any."""
    return customer_id is None or layout.customer_id == customer_id


def default_for(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None,
                kind: str = layouts.ISSUED_ITEMS) -> ImportLayout | None:
    """The customer's default, else the bank's."""
    owners = ([customer_id] if customer_id is not None and kind == layouts.ISSUED_ITEMS else []) + [None]
    for owner in owners:
        stmt = select(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == kind, ImportLayout.is_default.is_(True))
        stmt = stmt.where(ImportLayout.customer_id.is_(None) if owner is None else ImportLayout.customer_id == owner)
        found = session.execute(stmt.limit(1)).scalar_one_or_none()
        if found is not None:
            return found
    return None


def save(session: Session, tenant_id: uuid.UUID, *, layout: ImportLayout | None, name: str, owner_customer_id: uuid.UUID | None,
         raw_settings: dict, is_default: bool, user_id: uuid.UUID | None, kind: str = layouts.ISSUED_ITEMS) -> ImportLayout:
    """Creates or changes a layout. Raises layouts.LayoutError. A layout keeps its kind."""
    kind = layout.kind if layout is not None else kind
    if kind not in KINDS:
        raise layouts.LayoutError("Choose what the file holds.")
    if kind == layouts.PAID_ITEMS:
        owner_customer_id = None  # the core's file covers every customer (PIL-D2)
    name = (name or "").strip()
    if not name:
        raise layouts.LayoutError("Give the layout a name.")
    if len(name) > 100:
        raise layouts.LayoutError("The name must be 100 characters or fewer.")
    settings = layouts.clean_settings(raw_settings, kind=kind)
    if layout is None:
        layout = ImportLayout(tenant_id=tenant_id, kind=kind, created_by_user_id=user_id)
        session.add(layout)
    layout.name, layout.customer_id, layout.settings, layout.is_default = name, owner_customer_id, settings, is_default
    session.flush()
    if is_default:
        owner = ImportLayout.customer_id.is_(None) if owner_customer_id is None else ImportLayout.customer_id == owner_customer_id
        session.execute(update(ImportLayout).where(ImportLayout.tenant_id == tenant_id, ImportLayout.kind == kind, owner,
                                                   ImportLayout.id != layout.id).values(is_default=False))
    return layout


def delete(session: Session, layout: ImportLayout) -> None:
    session.delete(layout)
    session.flush()


def upload_options(layout: ImportLayout) -> dict:
    """What an upload keeps: which layout, and a copy of its settings, so editing the layout
    later never changes what an earlier upload meant."""
    return {"layout": {"id": str(layout.id), "name": layout.name, "settings": {**layout.settings, "kind": layout.kind}}}
