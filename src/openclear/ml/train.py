# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.decision import Decision, DecisionOutcome, DecisionSource
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.domain.ml_model import MlModel, MlModelStatus
from openclear.ml.registry import ArtifactStore, activate_model, create_model_row, get_active_model_row

MIN_DECISIONS_TO_TRAIN = 10
_HOLDOUT_FRACTION = 0.2


class InsufficientTrainingData(Exception):
    pass


class RetrainCooldownActive(Exception):
    """Raised when this exact model slot was retrained too recently — see
    config.Settings.ml_retrain_cooldown_seconds. One choke point in train_model itself
    so it applies uniformly to the web routes, the API routes, and the scheduled job,
    closing off repeated on-demand retraining as a self-service compute DoS."""


def _seconds_since(session: Session, occurred_at: datetime) -> float:
    """Elapsed time measured on the database's own clock: `occurred_at` is a
    server_default now(), so comparing it with this process's clock would shift the
    cooldown by however far the two clocks disagree (and a VM's clock can drift by
    seconds or more). SQLite drops tzinfo on reload (same caveat as
    audit_log_service.py::_normalize_datetime); every value here is UTC by construction."""
    database_now = session.execute(select(func.now())).scalar_one()
    if isinstance(database_now, str):  # SQLite without type processing
        database_now = datetime.fromisoformat(database_now)
    if database_now.tzinfo is None:
        database_now = database_now.replace(tzinfo=timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return max(0.0, (database_now - occurred_at).total_seconds())


@dataclass(frozen=True, slots=True)
class TrainResult:
    model_row: MlModel
    promoted: bool
    metrics: dict[str, float]


def _labeled_decisions(stmt, network_code: str, tenant_id: uuid.UUID):
    """Adds to `stmt` (a select over Decision) a bank's training data (SIMPLIFY_PLAN.md SP-D8):
    its own final decisions on this network, made by a person. A default applied because nobody
    decided in time (DecisionSource.AUTO_DEFAULT) says what the account's setting is, not whether
    the item was fraud, so it would teach the model the default (REGULATORY_PLAN.md RG6)."""
    return stmt.join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id).where(
        ExceptionItem.tenant_id == tenant_id,
        ExceptionItem.network_code == network_code,
        ExceptionItem.source == ExceptionItemSource.LIVE,
        Decision.features_json.is_not(None),
        # Only final decisions: one that can still change before its cut-off isn't a label yet (UI_PLAN.md UI-D13).
        Decision.locked_at.is_not(None),
        Decision.source == DecisionSource.HUMAN,
    )


def count_labeled_decisions(session: Session, network_code: str, *, tenant_id: uuid.UUID) -> int:
    """Every decision the slot could learn from, before the training window and row cap:
    what the scheduled retrain compares against the last run's count to see how many are
    new (workers/tasks.py)."""
    stmt = _labeled_decisions(select(func.count(Decision.id)), network_code, tenant_id)
    return session.execute(stmt).scalar_one()


@dataclass(frozen=True, slots=True)
class TrainingData:
    features: list[dict]
    labels: list[int]
    eligible: int  # count_labeled_decisions at the time
    window_days: int
    max_rows: int

    @property
    def capped(self) -> bool:
        return len(self.labels) < self.eligible


def _load_training_data(session: Session, network_code: str, *, tenant_id: uuid.UUID) -> TrainingData:
    """The slot's training rows, oldest first (the holdout is the newest fifth): only the
    two columns training needs, read in batches rather than as Decision objects, from
    the last ml_training_window_days, capped at the newest ml_training_max_rows."""
    settings = get_settings()
    window_days, max_rows = settings.ml_training_window_days, settings.ml_training_max_rows
    stmt = _labeled_decisions(select(Decision.features_json, Decision.outcome), network_code, tenant_id)
    if window_days > 0:
        cutoff = session.execute(select(func.now())).scalar_one() - timedelta(days=window_days)
        stmt = stmt.where(Decision.decided_at >= cutoff)
    # Newest first so the cap keeps the most recent rows; id breaks ties so the split is
    # reproducible. Reversed below.
    stmt = stmt.order_by(Decision.decided_at.desc(), Decision.id.desc()).limit(max_rows)
    features, labels = [], []
    for features_json, outcome in session.execute(stmt.execution_options(yield_per=5000)):
        features.append(features_json)
        labels.append(1 if outcome == DecisionOutcome.PAY else 0)
    features.reverse()
    labels.reverse()
    return TrainingData(
        features, labels, count_labeled_decisions(session, network_code, tenant_id=tenant_id), window_days, max_rows
    )


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
) -> tuple[bool, dict]:
    """Champion/challenger: should the newly trained model replace the active one?

    Both models are scored on the SAME held-out decisions (the most recent ones), so the
    comparison is fair — comparing a new model's AUC against the old model's stored AUC,
    measured on a different holdout, isn't. There's also a floor on how much of the bank's
    data a replacement has learned from (ml_bank_model_min_decisions): a small sample can
    "win" a small holdout by luck. The floor applies to the first model too (REGULATORY_PLAN.md
    RG6; docs/compliance/sr-11-7.md): it's kept, but not activated, until the bank has enough.
    Returns (promote, evaluation details)."""
    evaluation: dict = {"holdout_size": len(y_holdout)}
    minimum = get_settings().ml_bank_model_min_decisions
    if decision_count < minimum:
        evaluation["reason"] = (
            f"Not activated: trained on {decision_count} of the bank's own decisions; "
            f"{minimum} are needed before {'it can be activated' if champion is None else 'it can replace the active model'}."
        )
        if champion is not None:
            evaluation["compared_with_version"] = champion.version
        return False, evaluation
    if champion is None:
        evaluation["reason"] = "No model was active, so this one was activated."
        return True, evaluation

    evaluation["compared_with_version"] = champion.version

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


def train_model(session: Session, network_code: str, *, tenant_id: uuid.UUID) -> TrainResult:
    """Trains a bank's own model for one network (SIMPLIFY_PLAN.md SP-D8). The new model is
    activated only if it wins the champion/challenger comparison (_promotion_decision);
    otherwise it's kept, not active, with the reason in metrics_json["evaluation"], and an
    admin can still activate it by hand."""
    scope = f"tenant_id={tenant_id!r}"
    cooldown_seconds = get_settings().ml_retrain_cooldown_seconds
    slot_models = select(MlModel.created_at).where(MlModel.network_code == network_code, MlModel.tenant_id == tenant_id)
    latest = session.execute(slot_models.order_by(MlModel.created_at.desc()).limit(1)).first()
    if latest is not None and _seconds_since(session, latest[0]) < cooldown_seconds:
        raise RetrainCooldownActive(
            f"network_code={network_code!r} ({scope}) was retrained less than "
            f"{cooldown_seconds}s ago — wait before retraining again."
        )

    started = time.perf_counter()
    data = _load_training_data(session, network_code, tenant_id=tenant_id)
    X, y = data.features, data.labels
    if len(y) < MIN_DECISIONS_TO_TRAIN:
        raise InsufficientTrainingData(
            f"Only {len(y)} labeled decisions for network_code={network_code!r} ({scope}), "
            f"need at least {MIN_DECISIONS_TO_TRAIN}."
        )
    loaded_seconds = time.perf_counter() - started

    split_index = max(1, int(len(y) * (1 - _HOLDOUT_FRACTION)))
    split_index = min(split_index, len(y) - 1)  # always leave >=1 holdout row
    X_train, X_holdout = X[:split_index], X[split_index:]
    y_train, y_holdout = y[:split_index], y[split_index:]

    from openclear import plugins

    algorithm = get_settings().ml_scoring_model
    model = plugins.lookup("scoring_models", algorithm, what="scoring model")()
    model.fit(X_train, y_train)
    metrics = _evaluate(model, X_holdout, y_holdout)

    champion = get_active_model_row(session, network_code, tenant_id=tenant_id)
    promote, evaluation = _promotion_decision(
        champion=champion, challenger_metrics=metrics, X_holdout=X_holdout, y_holdout=y_holdout, decision_count=len(y))

    existing_count = session.execute(select(func.count()).select_from(slot_models.subquery())).scalar_one()
    version = f"v{existing_count + 1}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    # Unique per run: a file is never overwritten, so an older row's pinned SHA-256 stays true
    # even when the same bank trains twice in one second (a demo reset does).
    artifact_key = f"{network_code}_bank_{tenant_id}_{version}_{uuid.uuid4().hex[:8]}"
    artifact_path, artifact_sha256 = ArtifactStore().save(model, key=artifact_key)

    model_row = create_model_row(
        session,
        network_code=network_code,
        tenant_id=tenant_id,
        version=version,
        algorithm=algorithm,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        trained_from_decision_count=len(y),
        metrics_json={
            **metrics,
            "evaluation": evaluation,
            # So growth is visible (SCALE_PLAN.md S5), and so the scheduled retrain can
            # count new decisions against everything eligible, not just the rows used.
            "training": {
                "rows": len(y),
                "eligible_decisions": data.eligible,
                "capped": data.capped,
                "window_days": data.window_days,
                "max_rows": data.max_rows,
                "load_seconds": round(loaded_seconds, 3),
                "seconds": round(time.perf_counter() - started, 3),
            },
        },
        status=MlModelStatus.TRAINING,
    )

    if promote:
        activate_model(session, model_row.id, expected_tenant_id=tenant_id)
    else:
        model_row.status = MlModelStatus.RETIRED

    session.commit()
    return TrainResult(model_row=model_row, promoted=promote, metrics=metrics)
