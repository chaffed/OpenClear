# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank's choice of fraud-scoring model: the shared network model or a bank-only model
(domain/tenant.py::MlModelSource, FIX_PLAN.md Phase 5).

- Every bank starts on the shared model. A bank created after this feature must stay on
  it for ml_new_bank_private_switch_lock_days (default 90) before switching to bank-only;
  the platform operator can lift that lock early (clear_switch_lock).
- Switching to bank-only copies the currently active shared model into the bank's own
  slot and activates it, so scoring never has a gap. From then on the copy only changes
  when the bank retrains it on its own decisions (champion/challenger, ml/train.py). The
  bank's decisions stop feeding the shared model from its next retrain
  (workers/tasks.py::retrain_job retrains it after any bank leaves).
- Switching back to the shared model is never locked, but needs the bank to acknowledge
  the data-pooling disclosure (config.Settings.ml_shared_pool_disclosure_text), which is
  recorded with who/when. Old bank-only models stay in history.

Callers audit-log each change (web/routers/tenant_ml.py)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.ml_model import MlModel, MlModelStatus
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.registry import ArtifactIntegrityError, ArtifactStore, activate_model, create_model_row, get_active_model_row
from openclear.networks.registry import registered_codes

logger = logging.getLogger(__name__)


class SwitchNotAllowed(ValueError):
    """The requested change isn't allowed right now. The message is safe to show."""


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on reload; stored values are always UTC by construction.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def private_switch_available_on(tenant: Tenant) -> datetime | None:
    """When a bank on the shared model may switch to bank-only, if that's still in the
    future; None if it can switch now."""
    allowed_at = _as_utc(tenant.ml_private_switch_allowed_at)
    if allowed_at is not None and allowed_at > datetime.now(timezone.utc):
        return allowed_at
    return None


def _seed_bank_model(session: Session, tenant: Tenant, shared: MlModel) -> MlModel:
    """Copies one network's active shared model into the bank's slot: the artifact file
    too, not just a pointer to it, so later clean-up of shared-model files can't break the
    bank's scoring."""
    version = f"seed-from-shared-{shared.version}"[:64]
    # Verified copy: a tampered shared file must not be re-fingerprinted as the bank's own.
    artifact_path, artifact_sha256 = ArtifactStore().copy_model(
        shared, f"{shared.network_code}_bank_{tenant.id}_{uuid.uuid4().hex[:8]}_{version}"
    )
    shared_metrics = {k: v for k, v in (shared.metrics_json or {}).items() if not isinstance(v, dict)}
    row = create_model_row(
        session,
        network_code=shared.network_code,
        tenant_id=tenant.id,
        version=version,
        algorithm=shared.algorithm,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        trained_from_decision_count=shared.trained_from_decision_count,
        metrics_json={
            **shared_metrics,
            "evaluation": {
                "reason": f"Copied from shared model {shared.version} when the bank switched to a bank-only model.",
                "seeded_from_model_id": str(shared.id),
                "seeded_from_version": shared.version,
            },
        },
        status=MlModelStatus.TRAINING,
    )
    activate_model(session, row.id, expected_customer_id=None, expected_tenant_id=tenant.id)
    return row


def switch_to_bank_only(session: Session, tenant_id: uuid.UUID, *, actor_user_id: uuid.UUID) -> list[MlModel]:
    """Returns the seeded bank models (one per network that had an active shared model;
    a network with none has nothing to copy, so it stays unscored until the bank trains
    its own). The caller commits."""
    tenant = session.get(Tenant, tenant_id)
    if tenant.ml_model_source == MlModelSource.PRIVATE:
        raise SwitchNotAllowed("This organization already uses a bank-only model.")
    available_on = private_switch_available_on(tenant)
    if available_on is not None:
        raise SwitchNotAllowed(
            f"New organizations use the shared model for their first "
            f"{get_settings().ml_new_bank_private_switch_lock_days} days. You can switch on "
            f"{available_on:%B} {available_on.day}, {available_on.year}."
        )

    seeded = []
    for network_code in registered_codes():
        shared = get_active_model_row(session, network_code)
        if shared is not None and shared.artifact_path and Path(shared.artifact_path).exists():
            try:
                seeded.append(_seed_bank_model(session, tenant, shared))
            except ArtifactIntegrityError:
                # Same outcome as having no shared model to copy: this network stays
                # unscored for the bank until it trains its own.
                logger.exception("Not seeding %s for bank %s: the shared model file failed its integrity check", network_code, tenant.id)
    tenant.ml_model_source = MlModelSource.PRIVATE
    tenant.ml_source_changed_at = datetime.now(timezone.utc)
    tenant.ml_source_changed_by_user_id = actor_user_id
    session.flush()
    return seeded


def switch_to_shared(session: Session, tenant_id: uuid.UUID, *, actor_user_id: uuid.UUID, consented: bool) -> None:
    tenant = session.get(Tenant, tenant_id)
    if tenant.ml_model_source == MlModelSource.SHARED:
        raise SwitchNotAllowed("This organization already uses the shared model.")
    if not consented:
        raise SwitchNotAllowed("Please confirm you've read how the shared model uses your data.")
    now = datetime.now(timezone.utc)
    tenant.ml_model_source = MlModelSource.SHARED
    tenant.ml_source_changed_at = now
    tenant.ml_source_changed_by_user_id = actor_user_id
    tenant.ml_shared_consent_at = now
    tenant.ml_shared_consent_by_user_id = actor_user_id
    session.flush()


def record_shared_consent(session: Session, tenant_id: uuid.UUID, *, actor_user_id: uuid.UUID) -> None:
    """A bank already on the shared model (every new bank, and banks that predate the
    consent record) acknowledging the data-pooling disclosure."""
    tenant = session.get(Tenant, tenant_id)
    tenant.ml_shared_consent_at = datetime.now(timezone.utc)
    tenant.ml_shared_consent_by_user_id = actor_user_id
    session.flush()


def clear_switch_lock(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    """Platform-operator override of the new-bank lock (api/v1/platform_ml.py)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.ml_private_switch_allowed_at = None
    session.flush()
    return tenant


def banks_left_shared_since(session: Session, since: datetime) -> bool:
    """Has any bank switched to bank-only since `since` (the shared model's training
    time)? Its data is still inside that shared model until the next retrain."""
    changed = session.execute(
        select(Tenant.ml_source_changed_at).where(
            Tenant.ml_model_source == MlModelSource.PRIVATE, Tenant.ml_source_changed_at.is_not(None)
        )
    ).scalars().all()
    since = _as_utc(since)
    return any(_as_utc(at) > since for at in changed)


@dataclass(frozen=True, slots=True)
class SharedModelSummary:
    """What a bank may see about the shared model: counts only, never which other banks
    contribute or anything about their data."""

    network_code: str
    version: str | None
    activated_at: datetime | None
    trained_from_decision_count: int | None
    contributing_bank_count: int


def shared_model_summaries(session: Session) -> list[SharedModelSummary]:
    summaries = []
    for network_code in registered_codes():
        contributing = session.execute(
            select(func.count(distinct(ExceptionItem.tenant_id)))
            .select_from(Decision)
            .join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id)
            .join(Tenant, Tenant.id == ExceptionItem.tenant_id)
            .where(
                ExceptionItem.network_code == network_code,
                Tenant.ml_model_source == MlModelSource.SHARED,
                Decision.features_json.is_not(None),
            )
        ).scalar_one()
        active = get_active_model_row(session, network_code)
        summaries.append(
            SharedModelSummary(
                network_code=network_code,
                version=active.version if active else None,
                activated_at=active.activated_at if active else None,
                trained_from_decision_count=active.trained_from_decision_count if active else None,
                contributing_bank_count=contributing,
            )
        )
    return summaries
