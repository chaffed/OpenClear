# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
import uuid
from collections import OrderedDict

from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.tenant import Tenant
from openclear.ml.model import ScoringModel
from openclear.ml.registry import ArtifactIntegrityError, ArtifactStore, get_active_model_row
from openclear.networks.registry import get_adapter

logger = logging.getLogger(__name__)

# In-process cache: (network_code, tenant_id) -> (active_model_id, loaded_model).
# Invalidated automatically whenever the DB's active model id for that slot changes (e.g.
# after a retrain promotes a new version) — no explicit cache-bust call needed elsewhere.
# Least recently used first, and trimmed to ml_model_cache_size.
_MODEL_CACHE: OrderedDict[tuple[str, uuid.UUID], tuple[uuid.UUID, ScoringModel]] = OrderedDict()


def _load_model(session: Session, tenant_id: uuid.UUID, network_code: str) -> tuple[ScoringModel, str] | None:
    """The bank's own active model, if it has scoring on and one is trained (SIMPLIFY_PLAN.md
    SP-D8, SP-D10)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.ml_scoring_enabled:
        return None
    model_row = get_active_model_row(session, network_code, tenant_id=tenant_id)
    if model_row is None:
        return None

    cache_key = (network_code, tenant_id)
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None and cached[0] == model_row.id:
        _MODEL_CACHE.move_to_end(cache_key)
        return cached[1], model_row.version

    try:
        model = ArtifactStore().load_model(model_row)
    except ArtifactIntegrityError:
        # Never let a bad model file block ingestion: the item just goes unscored, the
        # same as before any model existed, and this is logged for the operator.
        logger.exception("Not scoring with ml_model %s: its artifact failed the integrity check", model_row.id)
        return None
    except OSError:
        # Likewise a model file that's missing or unreadable (a volume not mounted, a
        # restore without the artifacts): otherwise every exception row would fail to load.
        logger.exception("Not scoring with ml_model %s: its artifact can't be read", model_row.id)
        return None
    _MODEL_CACHE[cache_key] = (model_row.id, model)
    _MODEL_CACHE.move_to_end(cache_key)
    while len(_MODEL_CACHE) > max(1, get_settings().ml_model_cache_size):
        _MODEL_CACHE.popitem(last=False)
    return model, model_row.version


def score_exception(session: Session, exception_item: ExceptionItem) -> float | None:
    """Scores an exception with the bank's own model, when it has scoring on and a model is
    trained. The score is advisory: nothing decides an item by it. Returns None (leaving
    exception_item.ml_score unset) otherwise — matching works fully without ML; a null score
    means "no score", never a fabricated 0.5, so the UI can tell the two apart."""
    loaded = _load_model(session, exception_item.tenant_id, exception_item.network_code)
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
