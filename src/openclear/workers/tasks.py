# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_session_factory
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.ml_model import MlModelStatus
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.registry import create_model_row, get_active_model_row, list_slot_models
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, count_labeled_decisions, train_model
from openclear.networks.registry import registered_codes
from openclear.notifications.email.factory import get_email_provider
from openclear.notifications.sms.factory import get_sms_provider
from openclear.services import auto_disposition_service, demo_tenant_service, tenant_ml_service
from openclear.services.dropbox_import_service import tenants_with_a_dropbox
from openclear.workers import job_handlers, jobs

logger = logging.getLogger(__name__)


def _count_labeled_decisions(session: Session, network_code: str, *, tenant_id: uuid.UUID | None = None) -> int:
    """How many decisions the shared model (tenant_id None) or one bank-only model could
    learn from — the exact same rules as training itself (ml/train.py::
    count_labeled_decisions), so e.g. a bank-only bank's decisions never trigger a
    shared-model retrain. A COUNT, not the rows."""
    return count_labeled_decisions(session, network_code, tenant_id=tenant_id)


def _most_recently_trained_count(
    session: Session, network_code: str, customer_id: uuid.UUID | None = None, *, tenant_id: uuid.UUID | None = None
) -> int:
    """How many eligible decisions the slot's last run saw. Since S5 a run may use fewer
    rows than that (the training window and row cap), so this is the count it recorded in
    metrics_json["training"], falling back to the rows used for models trained before."""
    models = [m for m in list_slot_models(session, network_code, tenant_id=tenant_id, customer_id=customer_id)
              if m.status != MlModelStatus.FAILED]
    if not models:
        return 0
    training = (models[0].metrics_json or {}).get("training") or {}
    return training.get("eligible_decisions", models[0].trained_from_decision_count)


def _customers_with_labeled_decisions(session: Session, network_code: str) -> dict[uuid.UUID, int]:
    """Every customer_id with at least one labeled decision for this network, and how
    many total — the same shape _count_labeled_decisions works with, just grouped by
    customer instead of network-wide. Bank-wide (customer_id is None) exceptions are
    covered by the existing global retrain above, not here."""
    stmt = (
        select(ExceptionItem.customer_id, func.count(Decision.id))
        .select_from(Decision)
        .join(ExceptionItem, Decision.exception_item_id == ExceptionItem.id)
        .where(
            ExceptionItem.network_code == network_code,
            Decision.features_json.is_not(None),
            ExceptionItem.customer_id.is_not(None),
        )
        .group_by(ExceptionItem.customer_id)
    )
    return dict(session.execute(stmt).all())


def _bank_left_since_shared_trained(session: Session, network_code: str) -> bool:
    active = get_active_model_row(session, network_code)
    # activated_at (set in Python, microsecond precision), not created_at (a database
    # default, whole seconds on SQLite), so a switch moments before a retrain isn't
    # mistaken for one after it.
    return active is not None and tenant_ml_service.banks_left_shared_since(
        session, active.activated_at or active.created_at
    )


def _train_and_log(
    session: Session,
    network_code: str,
    *,
    customer_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    label: str = "shared",
    force_activate_reason: str | None = None,
) -> None:
    """Runs train_model for one (network_code, customer_id) pair as part of the
    unattended scheduled job, and never lets a single network/customer's failure abort
    the rest of the run — unlike the on-demand web/API routes (where a human is watching
    the request and an HTTP error is enough), an uncaught exception here would otherwise
    silently skip every network/customer still left in retrain_job()'s loops. An
    unexpected failure (not just the two already-expected "nothing to do yet" outcomes)
    is recorded as a FAILED MlModel row so it's visible on the admin models page, not
    just a server log line nobody looks at."""
    try:
        result = train_model(
            session, network_code, customer_id=customer_id, tenant_id=tenant_id, force_activate_reason=force_activate_reason
        )
        logger.info("Retrained %s (%s): promoted=%s metrics=%s", network_code, label, result.promoted, result.metrics)
    except InsufficientTrainingData as exc:
        logger.info("Skipping retrain for %s (%s): %s", network_code, label, exc)
    except RetrainCooldownActive as exc:
        logger.info("Retrain for %s (%s) still in cooldown: %s", network_code, label, exc)
    except Exception as exc:  # noqa: BLE001 — isolate one network/customer's failure from the rest of the run
        logger.exception("Retrain failed for %s (%s)", network_code, label)
        session.rollback()
        create_model_row(
            session,
            network_code=network_code,
            customer_id=customer_id,
            tenant_id=tenant_id,
            version=f"failed_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}",
            algorithm="logistic_regression",
            artifact_path="",
            trained_from_decision_count=0,
            metrics_json={"error": str(exc)},
            status=MlModelStatus.FAILED,
        )
        session.commit()


def retrain_job() -> None:
    """Iterates every registered network and retrains each model slot only if enough NEW
    labeled decisions have accumulated since its last training run — avoids thrashing on
    tiny batches. In order: the shared model (decisions from banks on the shared model),
    then each bank-only bank's own model, then per customer: any customer with enough
    new labeled decisions of their own gets their own model trained too, which is what
    actually builds a customer's first model and (via ml/predict.py's AUTO mode) puts it
    into use with no separate action required. Designed to be triggered by an in-process
    APScheduler cron (see workers/scheduler.py, zero-extra-infra for low-barrier
    deployments) or an external cron/Celery-beat/k8s CronJob for enterprise deployments —
    this function is the only thing that needs to be invoked either way."""
    settings = get_settings()
    session = get_session_factory()()
    try:
        private_bank_ids = session.execute(
            select(Tenant.id).where(Tenant.ml_model_source == MlModelSource.PRIVATE, Tenant.is_active)
        ).scalars().all()
        for network_code in registered_codes():
            for bank_id in [None, *private_bank_ids]:
                label = "shared" if bank_id is None else f"bank tenant_id={bank_id}"
                total = _count_labeled_decisions(session, network_code, tenant_id=bank_id)
                new_decisions = total - _most_recently_trained_count(session, network_code, tenant_id=bank_id)
                if bank_id is None and _bank_left_since_shared_trained(session, network_code):
                    # A bank switched to bank-only since the shared model was trained: its
                    # data is still inside that model until it's retrained without it.
                    _queue_retrain(
                        session, network_code, label="shared (a bank left)",
                        force_activate_reason=(
                            "Activated without the usual comparison: a bank switched to a bank-only model, "
                            "and the previous shared model still contained its data."
                        ),
                    )
                    continue
                if new_decisions < settings.ml_min_new_decisions_for_retrain:
                    logger.info(
                        "Skipping %s retrain for %s: %d new decisions, need %d",
                        label, network_code, new_decisions, settings.ml_min_new_decisions_for_retrain,
                    )
                    continue
                _queue_retrain(session, network_code, tenant_id=bank_id, label=label)

            for customer_id, customer_total in _customers_with_labeled_decisions(session, network_code).items():
                customer_already_trained_on = _most_recently_trained_count(session, network_code, customer_id)
                customer_new_decisions = customer_total - customer_already_trained_on
                if customer_new_decisions < settings.ml_min_new_decisions_for_retrain:
                    continue
                _queue_retrain(session, network_code, customer_id=customer_id, label=f"customer_id={customer_id}")
        session.commit()
        jobs.jobs_queued(session.get_bind())
    finally:
        session.close()


def _queue_retrain(
    session: Session,
    network_code: str,
    *,
    tenant_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    label: str,
    force_activate_reason: str | None = None,
) -> None:
    """One job per model slot, so slots train in parallel on however many workers there
    are. The job is bound to the slot's bank (a customer model's bank is found from its
    exceptions), so RLS applies while it trains; the shared model has no bank."""
    job_bank = tenant_id
    if customer_id is not None:
        job_bank = session.execute(
            select(ExceptionItem.tenant_id).where(ExceptionItem.customer_id == customer_id).limit(1)
        ).scalar_one_or_none()
    slot = f"customer:{customer_id}" if customer_id else f"bank:{tenant_id}" if tenant_id else "shared"
    jobs.enqueue(
        session,
        job_handlers.RETRAIN_MODEL_SLOT,
        tenant_id=job_bank,
        payload={
            "network_code": network_code,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "customer_id": str(customer_id) if customer_id else None,
            "label": label,
            "force_activate_reason": force_activate_reason,
        },
        dedupe_key=f"retrain:{network_code}:{slot}",
    )


def dropbox_import_job() -> None:
    """The in-app scheduler's entry point for the auto-import dropbox (see
    services/dropbox_import_service.py): queues one scan per bank, so banks are scanned
    in parallel across workers and one bank's large drop doesn't hold up the rest. A bank
    whose previous scan hasn't run yet isn't queued twice. (scripts/import_dropbox.py
    still scans every bank directly.)"""
    session = get_session_factory()()
    try:
        for tenant in tenants_with_a_dropbox(session):
            jobs.enqueue(session, job_handlers.SCAN_BANK_DROPBOX, tenant_id=tenant.id, dedupe_key=f"dropbox:{tenant.id}")
        session.commit()
        jobs.jobs_queued(session.get_bind())
    finally:
        session.close()


def demo_reset_job() -> None:
    """Resets the public demo organization on a fixed schedule
    (config.Settings.demo_tenant_reset_interval_minutes). The login-time idle reset
    (services/demo_tenant_service.py::maybe_reset_if_demo_idle_by_slug) only fires once
    nobody has signed in for a while, which never happens while a visitor keeps using
    it — so without this, whatever one visitor changed would stay in place for everyone
    else. Anyone signed in at the moment of a reset is sent back to the sign-in page.
    A no-op if there's no demo organization or no demo password configured."""
    session = get_session_factory()()
    try:
        if demo_tenant_service.get_demo_tenant(session) is None:
            return
        demo_tenant_service.reset_demo_tenant(session)
        logger.info("Scheduled demo reset complete")
    except demo_tenant_service.DemoTenantNotConfigured as exc:
        logger.warning("Scheduled demo reset skipped: %s", exc)
    except Exception:  # noqa: BLE001 -- a failed reset must never take the scheduler down
        logger.exception("Scheduled demo reset failed")
        session.rollback()
    finally:
        session.close()


def sweep_expired_dispositions_job() -> None:
    """Finds every OPEN/PENDING_APPROVAL exception whose decision_deadline has passed and
    auto-decides it per its customer's CustomerDispositionSetting (services/
    auto_disposition_service.py) -- one full cross-tenant pass, same "no per-tenant loop"
    shape as every other job here. A PENDING_APPROVAL exception (a maker's recommendation
    already awaiting a checker) is swept the same as OPEN -- the deadline is a hard
    backstop regardless of in-flight working state. Per-row try/except + per-row commit so
    one bad row can't lose the rest of the batch, same isolation as notification_dispatch_job.
    auto_decide_exception() returning None means "leave it open, try again next sweep"
    (no model/score yet, or no configured ACH return reason) -- not a failure."""
    session = get_session_factory()()
    try:
        now = datetime.now(timezone.utc)
        rows = (
            session.execute(
                select(ExceptionItem).where(
                    ExceptionItem.status.in_([ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL]),
                    ExceptionItem.decision_deadline.is_not(None),
                    ExceptionItem.decision_deadline <= now,
                )
            )
            .scalars()
            .all()
        )

        decided = 0
        skipped = 0
        for exception_item in rows:
            # Postgres RLS: the settings and the paid item / ACH transaction it updates
            # belong to this exception's bank.
            bind_session_to_tenant(session, exception_item.tenant_id)
            try:
                decision = auto_disposition_service.auto_decide_exception(session, exception_item)
            except Exception:  # noqa: BLE001 -- isolate one exception's failure from the rest of the sweep
                logger.exception("Auto-disposition failed for exception %s", exception_item.id)
                session.rollback()
                continue
            session.commit()
            if decision is not None:
                decided += 1
            else:
                skipped += 1

        if rows:
            logger.info("Disposition sweep: %d auto-decided, %d skipped (no model/reason configured yet)", decided, skipped)
    finally:
        session.close()


def _send_one_notification(notification: Notification) -> None:
    if notification.channel == NotificationChannel.EMAIL:
        get_email_provider().send(to=notification.destination, subject=notification.subject or "", body=notification.body)
    else:
        get_sms_provider().send(to=notification.destination, body=notification.body)


def notification_dispatch_job() -> None:
    """Drains PENDING Notification rows (services/notification_service.py queues them,
    never sends) -- one row's failure never blocks the rest of the batch, same isolation
    principle as _train_and_log/dropbox_import_job's own per-item try/except. Committed
    per-row so partial progress survives a crash mid-run rather than re-sending
    everything already-successful on the next poll."""
    settings = get_settings()
    session = get_session_factory()()
    try:
        rows = (
            session.execute(
                select(Notification)
                .where(Notification.status == NotificationStatus.PENDING)
                .order_by(Notification.created_at)
                .limit(settings.notification_dispatch_batch_size)
            )
            .scalars()
            .all()
        )

        sent = 0
        retrying = 0
        failed = 0
        for notification in rows:
            try:
                _send_one_notification(notification)
            except Exception as exc:  # noqa: BLE001 -- one bad destination/provider hiccup must never abort the batch
                notification.attempt_count += 1
                notification.error = str(exc)[:1000]
                if notification.exhausted_retries:
                    notification.status = NotificationStatus.FAILED
                    failed += 1
                else:
                    retrying += 1  # stays PENDING -- picked up again next poll
                logger.warning(
                    "Failed to send notification %s (%s, attempt %d, %s): %s",
                    notification.id, notification.channel.value, notification.attempt_count,
                    "giving up" if notification.exhausted_retries else "will retry", exc,
                )
            else:
                notification.status = NotificationStatus.SENT
                notification.sent_at = datetime.now(timezone.utc)
                sent += 1
            session.commit()

        if rows:
            logger.info("Notification dispatch: %d sent, %d retrying, %d permanently failed", sent, retrying, failed)
    finally:
        session.close()


def housekeeping_job() -> None:
    """Daily clean-up of operational rows (services/housekeeping_service.py): finished
    jobs, sent notifications, old bulk-upload row results and expired export archives."""
    from openclear.services import housekeeping_service

    session = get_session_factory()()
    try:
        housekeeping_service.run(session)
    finally:
        session.close()
