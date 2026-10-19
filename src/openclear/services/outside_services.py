# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The outside services OpenClear uses for a bank (CONTROLS_PLAN.md CT3), and when the bank last
reviewed each (Tenant.outside_service_reviews). Evidence for B-SA-9-01: the bank assesses each
outside service it relies on, and the outside_services check warns when one hasn't been reviewed
within a year.

A service is listed only when it's actually in use: email only with an SMTP host or another
provider configured, SMS only with its credentials, OCR and storage when they aren't the
built-in, local ones, SIEM destinations and identity providers when set up."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings

REVIEW_EVERY_DAYS = 365


@dataclass
class OutsideService:
    key: str
    label: str
    detail: str
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    note: str | None = None


def in_use(session: Session, tenant_id: uuid.UUID) -> list[OutsideService]:
    from openclear.domain.security_event_destination import SecurityEventDestination
    from openclear.domain.sso_connection import SsoConnection
    from openclear.domain.tenant import Tenant

    settings = get_settings()
    found: list[OutsideService] = []
    if settings.email_provider != "smtp" or settings.smtp_host:
        found.append(OutsideService("email", "Email delivery", settings.email_provider + (f" ({settings.smtp_host})" if settings.smtp_host else "")))
    if settings.twilio_account_sid or settings.sms_provider != "twilio":
        found.append(OutsideService("sms", "Text messages", settings.sms_provider))
    if settings.ocr_provider != "tesseract":
        found.append(OutsideService("ocr", "Check image reading (OCR)", settings.ocr_provider))
    if settings.storage_backend != "local":
        found.append(OutsideService("storage", "File storage", settings.storage_backend))
    if settings.malware_clamd_address:
        found.append(OutsideService("malware_clamav", "Malware scanning of uploads", "ClamAV"))
    if settings.malware_virustotal_api_key:
        found.append(OutsideService("malware_virustotal", "Malware lookups of uploads", "VirusTotal (file hashes only, never files)"))
    if settings.siem_kind:
        found.append(OutsideService("siem_installation", "The installation's security event destination", settings.siem_kind))
    destination = session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id == tenant_id,
                                                                         SecurityEventDestination.enabled.is_(True))).scalar_one_or_none()
    if destination is not None:
        found.append(OutsideService("siem_bank", "Your security event destination", destination.kind))
    for connection in session.execute(select(SsoConnection).where(SsoConnection.tenant_id == tenant_id, SsoConnection.is_active.is_(True),
                                                                  SsoConnection.managed_by == "bank")).scalars():
        found.append(OutsideService(f"sso:{connection.id}", f"Identity provider: {connection.display_name}", connection.issuer or ""))
    reviews = session.get(Tenant, tenant_id).outside_service_reviews or {}
    for service in found:
        record = reviews.get(service.key) or {}
        if record.get("at"):
            service.reviewed_at = datetime.fromisoformat(record["at"])
            service.reviewed_by, service.note = record.get("by"), record.get("note")
    return found


def mark_reviewed(session: Session, tenant_id: uuid.UUID, key: str, *, by: str, note: str = "") -> OutsideService:
    from openclear.domain.tenant import Tenant

    service = next((s for s in in_use(session, tenant_id) if s.key == key), None)
    if service is None:
        raise ValueError("That isn't a service OpenClear uses for you.")
    tenant = session.get(Tenant, tenant_id)
    reviews = dict(tenant.outside_service_reviews or {})
    reviews[key] = {"at": datetime.now(timezone.utc).isoformat(), "by": by, "note": (note or "").strip()[:500]}
    tenant.outside_service_reviews = reviews
    session.flush()
    return service
