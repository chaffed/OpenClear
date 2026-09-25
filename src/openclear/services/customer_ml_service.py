# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.customer_ml_setting import CustomerMlSetting, MlScoringMode
from openclear.domain.ml_model import MlModel
from openclear.ml.predict import _bank_level_model, _resolve_scoring_source
from openclear.ml.registry import get_active_model_row
from openclear.networks.registry import registered_codes
from openclear.repositories.customer_ml_setting_repo import CustomerMlSettingRepository


@dataclass(frozen=True, slots=True)
class CustomerNetworkMlSummary:
    """One row per registered network for a given customer — what the "ML scoring" card
    on the customer detail page and the per-customer admin page are both built from."""

    network_code: str
    mode: MlScoringMode
    active_customer_model: MlModel | None
    # What the customer falls back to (or, in GLOBAL mode, always uses): its bank's
    # choice — the bank-only model or the shared model (ml/predict.py::_bank_level_model).
    active_bank_level_model: MlModel | None
    # The model that actually scores this customer's exceptions right now, if any.
    effective_model: MlModel | None


def get_mode(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, network_code: str) -> MlScoringMode:
    """Absence of a row means AUTO — see CustomerMlSetting's docstring."""
    settings = CustomerMlSettingRepository(session, tenant_id).list(customer_id=customer_id, network_code=network_code)
    return settings[0].mode if settings else MlScoringMode.AUTO


def set_mode(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, network_code: str, mode: MlScoringMode
) -> CustomerMlSetting:
    repo = CustomerMlSettingRepository(session, tenant_id)
    existing = repo.list(customer_id=customer_id, network_code=network_code)
    if existing:
        setting = existing[0]
        setting.mode = mode
    else:
        setting = CustomerMlSetting(customer_id=customer_id, network_code=network_code, mode=mode)
        repo.add(setting)
    session.flush()
    return setting


def list_customer_models(session: Session, customer_id: uuid.UUID, network_code: str | None = None) -> list[MlModel]:
    """This customer's own model history (every status, not just active) — for the
    per-customer admin detail page's model table, same shape as the global ml_models.html
    table."""
    stmt = select(MlModel).where(MlModel.customer_id == customer_id).order_by(MlModel.created_at.desc())
    if network_code is not None:
        stmt = stmt.where(MlModel.network_code == network_code)
    return list(session.execute(stmt).scalars().all())


def get_ml_summary(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[CustomerNetworkMlSummary]:
    """One entry per registered payment network — what's shown on the customer detail
    page's "ML scoring" card."""
    return [
        CustomerNetworkMlSummary(
            network_code=network_code,
            mode=get_mode(session, tenant_id, customer_id, network_code),
            active_customer_model=get_active_model_row(session, network_code, customer_id),
            active_bank_level_model=_bank_level_model(session, tenant_id, network_code),
            effective_model=_resolve_scoring_source(session, tenant_id, network_code, customer_id),
        )
        for network_code in registered_codes()
    ]
