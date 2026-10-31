# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank's own forms for its customers (SIMPLIFY_PLAN.md SP1): today its Written Statement of
Unauthorized Debit. OpenClear doesn't collect or sign the statement; the bank does, its own way,
and OpenClear shows its customers the bank's form: Markdown, HTML, or a PDF to download."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear import storage
from openclear.domain.bank_form import BankForm

WSUD = "wsud"
FORMATS = ("markdown", "html", "pdf")
MAX_TEXT = 200_000
MAX_PDF_BYTES = 10 * 1024 * 1024


class FormError(UserFacingError):
    """The message is fit to show."""


def get(session: Session, tenant_id: uuid.UUID, key: str = WSUD) -> BankForm | None:
    return session.execute(select(BankForm).where(BankForm.tenant_id == tenant_id, BankForm.key == key)).scalar_one_or_none()


def save(session: Session, tenant_id: uuid.UUID, *, key: str = WSUD, format: str, body: str | None, pdf: bytes | None = None,
         filename: str | None = None, user_id: uuid.UUID | None) -> BankForm:
    """Sets the form. For a PDF, `pdf` is the new file, or None to keep the one already saved;
    `body` is then an optional note shown above it. Raises FormError."""
    if format not in FORMATS:
        raise FormError("Choose Markdown, HTML or a PDF.")
    body = (body or "").strip()
    if len(body) > MAX_TEXT:
        raise FormError("That's too long for a form: keep it under 200,000 characters.")
    form = get(session, tenant_id, key)
    if format == "pdf":
        if pdf is not None:
            if not pdf.startswith(b"%PDF-"):
                raise FormError("That file isn't a PDF.")
            if len(pdf) > MAX_PDF_BYTES:
                raise FormError("A PDF form can be up to 10 MB.")
        elif form is None or form.storage_ref is None:
            raise FormError("Choose the PDF to upload.")
    elif not body:
        raise FormError("Write the form, or upload a PDF instead.")
    if form is None:
        form = BankForm(tenant_id=tenant_id, key=key, format=format)
        session.add(form)
    old_ref = form.storage_ref
    form.format, form.body, form.updated_by_user_id = format, body or None, user_id
    if format == "pdf" and pdf is not None:
        name = storage.safe_name(filename or "form.pdf", "form.pdf")
        form.storage_ref = storage.put("tenant_assets", f"{tenant_id}/forms/{key}-{uuid.uuid4().hex}-{name}", pdf)
        form.filename = name if name.lower().endswith(".pdf") else f"{name}.pdf"
    elif format != "pdf":
        form.storage_ref, form.filename = None, None
    session.flush()
    if old_ref and old_ref != form.storage_ref:
        try:
            storage.delete(old_ref)
        except Exception:  # noqa: BLE001 -- a leftover file is harmless; the row no longer points at it
            pass
    return form


def remove(session: Session, tenant_id: uuid.UUID, key: str = WSUD) -> bool:
    form = get(session, tenant_id, key)
    if form is None:
        return False
    ref = form.storage_ref
    session.delete(form)
    session.flush()
    if ref:
        try:
            storage.delete(ref)
        except Exception:  # noqa: BLE001
            pass
    return True
