# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pospay.config import get_settings
from pospay.domain.decision import Decision, DecisionOutcome
from pospay.domain.exception_item import ExceptionItem, ExceptionItemSource
from pospay.domain.ml_model import MlModel, MlModelStatus
from pospay.domain.tenant import MlModelSource, Tenant
from pospay.ml.model import LogisticRegressionScoringModel
from pospay.ml.registry import ArtifactStore, activate_model, create_model_row, get_active_model_row

MIN_DECISIONS_TO_TRAIN = 10
_HOLDOUT_FRACTION = 0.2


class InsufficientTrainingData(Exception):
    pass


class RetrainCooldownActive(Exception):
    """Raised when this exact model slot was retrained too recently — see
    config.Settings.ml_retrain_cooldown_seconds. One choke point in train_model itself
    so it applies uniformly to the web routes, the API routes, and the scheduled job,
    closing off repeated on-demand retraining as a self-service compute DoS."""


def _seconds_since(occurred_at: datetime) -> float:
    # SQLite drops tzinfo on reload (same caveat audit_log_service.py::_normalize_datetime
    # documents) — a naive value here is always already-UTC by construction.
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    # occurred_at can come from the database server's clock (server_default now()), which
    # may run slightly ahead of this one; never report negative elapsed time.
    return max(0.0, (datetime.now(timezone.utc) - occurred_at).total_seconds())


@dataclass(frozen=True, slots=True)
class TrainResult:
    model_row: MlModel
    promoted: bool
    metrics: dict[str, float]


def _scope_label(tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None) -> str:
    if customer_id is not None:
        return f"customer_id={customer_id!r}"
    if tenant_id is not None:
        return f"the bank-only model for tenant_id={tenant_id!r}"
    return "the shared model"


def _load_labeled_decisions(
    session: Session, network_code: str, customer_id: uuid.UUID | None = None, *, tenant_id: uuid.UUID | None = None
) -> list[Decision]:
    """The training data for one model slot (see ml/registry.py), oldest first:

    - a customer's model: that customer's decisions;
    - a bank-only model: every decision at that bank, fraud-training examples included;
    - the shared model: decisions from banks on the SHARED model only — a bank that chose
      a bank-only model never contributes — and, from those banks, fraud-training examples
      only once the platform operator has approved them (shared_training_approved_at), so
      one bank can't quietly skew scoring for every other bank."""
    stmt = (
        select(Decision)
        .join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id)
        .where(
            ExceptionItem.network_code == network_code,
            Decision.features_json.is_not(None),
            ExceptionItem.retracted_at.is_(None),
        )
        .order_by(Decision.decided_at)
    )
    if customer_id is not None:
        stmt = stmt.where(ExceptionItem.customer_id == customer_id)
    elif tenant_id is not None:
        stmt = stmt.where(ExceptionItem.tenant_id == tenant_id)
    else:
        stmt = stmt.join(Tenant, Tenant.id == ExceptionItem.tenant_id).where(
            Tenant.ml_model_source == MlModelSource.SHARED,
            or_(
                ExceptionItem.source != ExceptionItemSource.TRAINING_BACKFILL,
                ExceptionItem.shared_training_approved_at.is_not(None),
            ),
        )
    return list(session.execute(stmt).scalars().all())


def _safe_metrics(y_true: list[int], y_pred_proba, y_pred: list[int]) -> dict[str, float]:
    from sklearn.metrics import precision_score, recall_score, roc_auc_score

    metrics = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
    }
    # AUC is undefined with only one class present in the holdout — common with small/
    # early datasets; skip rather than crash the whole training run over it.
    if len(set(y_true)) > 1:
        metrics["auc"] = float(roc_auc_score(y_true, y_pred_proba))
    return metrics


def _evaluate(model, X, y) -> dict[str, float]:
    proba = model.predict_proba(X)
    return _safe_metrics(y, proba, [1 if p >= 0.5 else 0 for p in proba])


def _promotion_decision(
    *,
    champion: MlModel | None,
    challenger_metrics: dict[str, float],
    X_holdout,
    y_holdout,
    decision_count: int,
    is_bank_model: bool,
) -> tuple[bool, dict]:
    """Champion/challenger: should the newly trained model replace the active one?

    Both models are scored on the SAME held-out decisions (the most recent ones), so the
    comparison is fair — comparing a new model's AUC against the old model's stored AUC,
    measured on a different holdout, isn't. For a bank-only model there's also a floor on
    how much of the bank's own data it's learned from (ml_bank_model_min_decisions): a
    bank's first own-data model has to beat the seeded copy of the shared model, and a
    small sample can "win" a small holdout by luck. Returns (promote, evaluation details)."""
    evaluation: dict = {"holdout_size": len(y_holdout)}
    if champion is None:
        evaluation["reason"] = "No model was active, so this one was activated."
        return True, evaluation

    evaluation["compared_with_version"] = champion.version
    minimum = get_settings().ml_bank_model_min_decisions
    if is_bank_model and decision_count < minimum:
        evaluation["reason"] = (
            f"Not activated: trained on {decision_count} of the bank's own decisions; "
            f"{minimum} are needed before it can replace the active model."
        )
        return False, evaluation

    try:
        champion_metrics = _evaluate(ArtifactStore().load_model(champion), X_holdout, y_holdout)
    except Exception:  # noqa: BLE001 -- a missing/unloadable old artifact shouldn't block training
        champion_metrics = {}
    challenger_auc, champion_auc = challenger_metrics.get("auc"), champion_metrics.get("auc")
    if challenger_auc is not None and champion_auc is not None:
        evaluation["active_model_auc_on_same_holdout"] = champion_auc
        promote = challenger_auc >= champion_auc
        evaluation["reason"] = (
            f"{'Activated' if promote else 'Not activated'}: AUC {challenger_auc:.3f} vs. "
            f"{champion_auc:.3f} for the active model on the same {len(y_holdout)} recent decisions."
        )
        return promote, evaluation

    # AUC isn't computable on this holdout (e.g. only one outcome in it): fall back to
    # the active model's recorded AUC.
    recorded = (champion.metrics_json or {}).get("auc", 0.0)
    promote = challenger_metrics.get("auc", 0.0) >= recorded
    evaluation["reason"] = (
        f"{'Activated' if promote else 'Not activated'}: compared with the active model's recorded AUC "
        f"({recorded:.3f}); the recent decisions didn't include both outcomes, so a same-data comparison wasn't possible."
    )
    return promote, evaluation


def train_model(
    session: Session,
    network_code: str,
    *,
    customer_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    force_activate_reason: str | None = None,
) -> TrainResult:
    """Trains one model slot (ml/registry.py): the shared model (neither id), a bank-only
    model (`tenant_id`), or a customer's model (`customer_id`; its bank is looked up if
    not given). The new model is activated only if it wins the champion/challenger
    comparison (_promotion_decision); otherwise it's kept, not active, with the reason in
    metrics_json["evaluation"], and an admin can still activate it by hand.

    `force_activate_reason` skips the comparison and activates the new model regardless,
    recording that reason: used when the active model must be replaced for a reason other
    than accuracy (a bank left the shared model, so the active model still contains data
    the bank withdrew)."""
    if customer_id is not None and tenant_id is None:
        # From the customer's exceptions, not the customer table: the scheduled retrain
        # has no bank bound, and under Postgres RLS the customer row isn't visible.
        tenant_id = session.execute(
            select(ExceptionItem.tenant_id).where(ExceptionItem.customer_id == customer_id).limit(1)
        ).scalar_one_or_none()
    slot_tenant_id = tenant_id if customer_id is None else None  # for the bank vs. shared distinction
    scope = _scope_label(slot_tenant_id, customer_id)

    cooldown_seconds = get_settings().ml_retrain_cooldown_seconds
    slot_models = select(MlModel.created_at).where(MlModel.network_code == network_code)
    if customer_id is not None:
        slot_models = slot_models.where(MlModel.customer_id == customer_id)
    else:
        slot_models = slot_models.where(MlModel.customer_id.is_(None), MlModel.tenant_id == slot_tenant_id)
    latest = session.execute(slot_models.order_by(MlModel.created_at.desc()).limit(1)).first()
    if latest is not None and _seconds_since(latest[0]) < cooldown_seconds:
        raise RetrainCooldownActive(
            f"network_code={network_code!r} ({scope}) was retrained less than "
            f"{cooldown_seconds}s ago — wait before retraining again."
        )

    decisions = _load_labeled_decisions(session, network_code, customer_id, tenant_id=slot_tenant_id)
    if len(decisions) < MIN_DECISIONS_TO_TRAIN:
        raise InsufficientTrainingData(
            f"Only {len(decisions)} labeled decisions for network_code={network_code!r} ({scope}), "
            f"need at least {MIN_DECISIONS_TO_TRAIN}."
        )

    X = [d.features_json for d in decisions]
    y = [1 if d.outcome == DecisionOutcome.PAY else 0 for d in decisions]

    split_index = max(1, int(len(decisions) * (1 - _HOLDOUT_FRACTION)))
    split_index = min(split_index, len(decisions) - 1)  # always leave >=1 holdout row
    X_train, X_holdout = X[:split_index], X[split_index:]
    y_train, y_holdout = y[:split_index], y[split_index:]

    model = LogisticRegressionScoringModel()
    model.fit(X_train, y_train)
    metrics = _evaluate(model, X_holdout, y_holdout)

    champion = get_active_model_row(session, network_code, customer_id, tenant_id=slot_tenant_id)
    if force_activate_reason is not None:
        promote, evaluation = True, {"holdout_size": len(y_holdout), "reason": force_activate_reason}
    else:
        promote, evaluation = _promotion_decision(
            champion=champion,
            challenger_metrics=metrics,
            X_holdout=X_holdout,
            y_holdout=y_holdout,
            decision_count=len(decisions),
            is_bank_model=customer_id is None and slot_tenant_id is not None,
        )

    existing_count = len(session.execute(slot_models).all())
    version = f"v{existing_count + 1}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    if customer_id is not None:
        artifact_key = f"{network_code}_{customer_id}_{version}"
    elif slot_tenant_id is not None:
        artifact_key = f"{network_code}_bank_{slot_tenant_id}_{version}"
    else:
        artifact_key = f"{network_code}_{version}"
    artifact_path, artifact_sha256 = ArtifactStore().save(model, key=artifact_key)

    model_row = create_model_row(
        session,
        network_code=network_code,
        customer_id=customer_id,
        tenant_id=tenant_id,
        version=version,
        algorithm="logistic_regression",
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        trained_from_decision_count=len(decisions),
        metrics_json={**metrics, "evaluation": evaluation},
        status=MlModelStatus.TRAINING,
    )

    if promote:
        activate_model(session, model_row.id, expected_customer_id=customer_id, expected_tenant_id=slot_tenant_id)
    else:
        model_row.status = MlModelStatus.RETIRED

    session.commit()
    return TrainResult(model_row=model_row, promoted=promote, metrics=metrics)
