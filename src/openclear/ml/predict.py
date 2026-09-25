# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
import uuid

from sqlalchemy.orm import Session

from openclear.domain.customer_ml_setting import MlScoringMode
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.model import ScoringModel
from openclear.ml.registry import ArtifactIntegrityError, ArtifactStore, get_active_model_row
from openclear.networks.registry import get_adapter
from openclear.repositories.customer_ml_setting_repo import CustomerMlSettingRepository

logger = logging.getLogger(__name__)

# In-process cache: model slot (network_code, tenant_id, customer_id) -> (active_model_id,
# loaded_model). Invalidated automatically whenever the DB's active model id for that
# slot changes (e.g. after a retrain promotes a new version) — no explicit cache-bust
# call needed elsewhere.
_MODEL_CACHE: dict[tuple[str, uuid.UUID | None, uuid.UUID | None], tuple[uuid.UUID, ScoringModel]] = {}


def _bank_level_model(session: Session, tenant_id: uuid.UUID, network_code: str) -> MlModel | None:
    """The model a bank's own choice points at (Tenant.ml_model_source): its bank-only
    model, or the shared network model. A bank-only bank never falls back to the shared
    model — its model is seeded from a copy of the shared one when it switches
    (services/tenant_ml_service.py), so the only way it has none is if there was no shared
    model to copy either, and then there's simply no score yet."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is not None and tenant.ml_model_source == MlModelSource.PRIVATE:
        return get_active_model_row(session, network_code, tenant_id=tenant_id)
    return get_active_model_row(session, network_code)


def _resolve_scoring_source(
    session: Session, tenant_id: uuid.UUID, network_code: str, customer_id: uuid.UUID | None
) -> MlModel | None:
    """Picks which MlModel row scores an exception:

    1. a customer-scoped exception uses its customer's own active model, per the
       customer's mode (below);
    2. otherwise the bank's choice: its bank-only model or the shared model
       (_bank_level_model).

    Customer mode — no CustomerMlSetting row means AUTO: use the customer's own model
    once one is active (the auto-switch: the moment a retrain promotes a customer's first
    model, the next exception picks it up), else the bank's model. CUSTOMER prefers the
    customer's model the same way. GLOBAL (labelled "Bank's model" in the UI) always uses
    the bank's model, ignoring the customer's own. Never an error: no model means no score."""
    if customer_id is not None:
        settings = CustomerMlSettingRepository(session, tenant_id).list(customer_id=customer_id, network_code=network_code)
        mode = settings[0].mode if settings else MlScoringMode.AUTO
        if mode != MlScoringMode.GLOBAL:
            customer_model = get_active_model_row(session, network_code, customer_id)
            if customer_model is not None:
                return customer_model
    return _bank_level_model(session, tenant_id, network_code)


def _load_model(session: Session, tenant_id: uuid.UUID, network_code: str, customer_id: uuid.UUID | None) -> tuple[ScoringModel, str] | None:
    model_row = _resolve_scoring_source(session, tenant_id, network_code, customer_id)
    if model_row is None:
        return None

    cache_key = (network_code, model_row.tenant_id, model_row.customer_id)
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None and cached[0] == model_row.id:
        return cached[1], model_row.version

    try:
        model = ArtifactStore().load_model(model_row)
    except ArtifactIntegrityError:
        # Never let a bad model file block ingestion: the item just goes unscored, the
        # same as before any model existed, and this is logged for the operator.
        logger.exception("Not scoring with ml_model %s: its artifact failed the integrity check", model_row.id)
        return None
    _MODEL_CACHE[cache_key] = (model_row.id, model)
    return model, model_row.version


def score_exception(session: Session, exception_item: ExceptionItem) -> float | None:
    """Scores an exception with whichever model (customer, bank-only, or shared — see
    _resolve_scoring_source) currently applies, if one exists yet. Returns None (leaving
    exception_item.ml_score unset) during cold start — matching rules/exceptions work
    fully without ML; a null score means 'not enough data yet to score this', never a
    fabricated 0.5, so the UI can distinguish the two."""
    loaded = _load_model(session, exception_item.tenant_id, exception_item.network_code, exception_item.customer_id)
    if loaded is None:
        return None
    model, version = loaded

    adapter = get_adapter(exception_item.network_code)
    features = adapter.build_features(session, exception_item)

    score = float(model.predict_proba([features])[0])
    exception_item.ml_score = score
    exception_item.ml_model_version = version
    return score


def reset_model_cache() -> None:
    """Test-only: force the next score_exception() call to reload from the DB/artifact."""
    _MODEL_CACHE.clear()
