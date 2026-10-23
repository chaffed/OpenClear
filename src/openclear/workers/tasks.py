# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_session_factory
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.ml_model import MlModelStatus
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus
from openclear.domain.tenant import Tenant
from openclear.ml.registry import create_model_row, list_slot_models
from openclear.ml.train import InsufficientTrainingData, RetrainCooldownActive, count_labeled_decisions, train_model
from openclear.networks.registry import registered_codes
from openclear.notifications.email.factory import get_email_provider
from openclear.notifications.sms.factory import get_sms_provider
from openclear.services import auto_disposition_service, demo_tenant_service
from openclear.services.dropbox_import_service import tenants_with_a_dropbox
from openclear.workers import job_handlers, jobs

logger = logging.getLogger(__name__)


def _most_recently_trained_count(session: Session, network_code: str, *, tenant_id: uuid.UUID) -> int:
    """How many eligible decisions the bank's last run saw (metrics_json["training"]), falling
    back to the rows used for models trained before that was recorded."""
    models = [m for m in list_slot_models(session, network_code, tenant_id=tenant_id) if m.status != MlModelStatus.FAILED]
    if not models:
        return 0
    training = (models[0].metrics_json or {}).get("training") or {}
    return training.get("eligible_decisions", models[0].trained_from_decision_count)


def _train_and_log(session: Session, network_code: str, *, tenant_id: uuid.UUID) -> None:
    """Trains one bank's model for one network as part of the unattended job, and never lets
    one bank's failure stop the others: an unexpected failure is recorded as a FAILED model
    row, so it shows on the bank's models page, not only in a log."""
    try:
        result = train_model(session, network_code, tenant_id=tenant_id)
        logger.info("Retrained %s for bank %s: promoted=%s metrics=%s", network_code, tenant_id, result.promoted, result.metrics)
    except InsufficientTrainingData as exc:
        logger.info("Skipping retrain for %s (bank %s): %s", network_code, tenant_id, exc)
    except RetrainCooldownActive as exc:
        logger.info("Retrain for %s (bank %s) still in cooldown: %s", network_code, tenant_id, exc)
    except Exception as exc:  # noqa: BLE001 — isolate one bank's failure from the rest of the run
        logger.exception("Retrain failed for %s (bank %s)", network_code, tenant_id)
        session.rollback()
        create_model_row(
            session, network_code=network_code, tenant_id=tenant_id,
            version=f"failed_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}", algorithm="logistic_regression", artifact_path="",
            trained_from_decision_count=0, metrics_json={"error": str(exc)}, status=MlModelStatus.FAILED,
        )
        session.commit()


def retrain_job() -> None:
    """Nightly: for every bank with fraud scoring on (SIMPLIFY_PLAN.md SP-D10), retrain its own
    model for each network once enough new final decisions have built up since the last run
    (ml_min_new_decisions_for_retrain). One job per bank and network, so they train in
    parallel on however many workers there are, each bound to its bank."""
    settings = get_settings()
    session = get_session_factory()()
    try:
        banks = session.execute(select(Tenant.id).where(Tenant.ml_scoring_enabled.is_(True), Tenant.is_active.is_(True))).scalars().all()
        for tenant_id in banks:
            bind_session_to_tenant(session, tenant_id)
            for network_code in registered_codes():
                new_decisions = (count_labeled_decisions(session, network_code, tenant_id=tenant_id)
                                 - _most_recently_trained_count(session, network_code, tenant_id=tenant_id))
                if new_decisions < settings.ml_min_new_decisions_for_retrain:
                    continue
                jobs.enqueue(session, job_handlers.RETRAIN_MODEL_SLOT, tenant_id=tenant_id,
                             payload={"network_code": network_code, "tenant_id": str(tenant_id)},
                             dedupe_key=f"retrain:{network_code}:bank:{tenant_id}")
            session.commit()
        jobs.jobs_queued(session.get_bind())
    finally:
        session.close()


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
    from openclear.services import reminder_service, webhook_service

    session = get_session_factory()()
    try:
        # First, warn about deadlines that are near (exception.deadline_approaching webhook).
        try:
            if warned := webhook_service.warn_deadlines_approaching(session):
                logger.info("Deadline warnings sent for %d exceptions", warned)
        except Exception:  # noqa: BLE001 -- the sweep itself matters more
            session.rollback()
            logger.exception("Deadline warnings failed")
        # Support access past its period ends, with an action-log entry (UX_PLAN.md U8).
        try:
            from openclear.services import support_access_service

            if support_access_service.expire_due(session):
                session.commit()
        except Exception:  # noqa: BLE001 -- the sweep itself matters more
            session.rollback()
            logger.exception("Expiring support access failed")
        # Settings changes waiting for a second person: expiry and the cooling-off (DUAL_CONTROL_PLAN.md),
        # in a session of its own, since it starts unbound for the service provider's.
        try:
            from openclear.services import change_request_service

            own = get_session_factory()()
            try:
                if settled := change_request_service.settle(own):
                    logger.info("Settled %d change requests", settled)
            finally:
                own.close()
        except Exception:  # noqa: BLE001 -- the sweep itself matters more
            logger.exception("Settling change requests failed")
        # Then the reminders to people (services/reminder_service.py; UX_PLAN.md U3).
        try:
            if reminded := reminder_service.send_due(session):
                logger.info("Deadline reminders queued for %d people", reminded)
        except Exception:  # noqa: BLE001 -- the sweep itself matters more
            session.rollback()
            logger.exception("Deadline reminders failed")
        now = datetime.now(timezone.utc)
        rows = session.execute(
            select(ExceptionItem.id, ExceptionItem.tenant_id).where(
                ExceptionItem.status.in_([ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL]),
                ExceptionItem.decision_deadline.is_not(None),
                ExceptionItem.decision_deadline <= now,
            )
        ).all()

        from openclear.repositories.exception_repo import ExceptionRepository

        decided = 0
        skipped = 0
        for exception_id, tenant_id in rows:
            # Postgres RLS: the settings and the paid item / ACH transaction it updates
            # belong to this exception's bank.
            bind_session_to_tenant(session, tenant_id)
            try:
                # The same row lock a person deciding takes (FULL_REVIEW_PLAN.md FR-D1): someone
                # deciding it this very moment wins, and the default isn't applied over them.
                exception_item = ExceptionRepository(session, tenant_id).get_for_update(exception_id, skip_locked=True)
                if exception_item is None:
                    session.rollback()
                    skipped += 1
                    continue
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

        # Decisions made before their cut-off lock at it, and go out (UI_PLAN.md UI-D13).
        try:
            from openclear.services import decision_service

            if locked := decision_service.lock_due(session):
                logger.info("Locked %d decisions at their cut-off", locked)
        except Exception:  # noqa: BLE001 -- tried again on the next run
            session.rollback()
            logger.exception("Locking decisions at the cut-off failed")
    finally:
        session.close()


def decision_lock_job() -> None:
    """Every minute (decision_lock_interval_seconds): decisions whose cut-off has passed become
    final and go out (services/decision_service.py::lock_due), so a core sees them within a
    minute of the cut-off rather than at the next deadline sweep (FULL_REVIEW_PLAN.md FR-D5)."""
    from openclear.services import decision_service

    session = get_session_factory()()
    try:
        if locked := decision_service.lock_due(session):
            logger.info("Locked %d decisions at their cut-off", locked)
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


def decision_outbox_job() -> None:
    """Writes each bank's decision file when its schedule says (services/decision_outbox_service.py)."""
    from openclear.services import decision_outbox_service

    session = get_session_factory()()
    try:
        written = decision_outbox_service.run_due(session)
        if written:
            logger.info("Decision outbox: %d file(s) written", written)
    finally:
        session.close()


def security_event_delivery_job() -> None:
    """Delivers security events to each destination from its cursor (services/siem/delivery.py)."""
    from openclear.services.siem import delivery

    session = get_session_factory()()
    try:
        if sent := delivery.deliver_due(session):
            logger.info("Security events: %d delivered", sent)
    finally:
        session.close()


def controls_watch_job() -> None:
    """Hourly: runs every bank's hourly checks and keeps each result (CONTROLS_PLAN.md CT2;
    services/control_results_service.py), so a change (to FAIL, say) is recorded, opens a
    deviation and becomes a security event even when nobody opens the Controls page. Checks
    that need a web request (the edge) are left to page views."""
    _run_controls("hourly")


def controls_nightly_job() -> None:
    """Nightly: the nightly state checks, and every population check over the previous UTC day;
    then, for each bank whose attestation period ended yesterday, that period's proposed CUEC
    attestations, and once a year has ended, the yearly request to customers to attest theirs
    for banks that ask (services/attestation_service.py)."""
    _run_controls("nightly")
    from openclear.services import attestation_service

    session = get_session_factory()()
    try:
        attestation_service.prepare_due(session)
        attestation_service.ask_customers_due(session)
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("Preparing attestations failed")
    finally:
        session.close()


def _run_controls(schedule: str) -> None:
    from openclear.compliance import checks
    from openclear.domain.tenant import Tenant
    from openclear.services import control_results_service, controls_service

    chosen = [c for c in checks.registry() if c.schedule == schedule and not c.needs_request]
    session = get_session_factory()()
    try:
        banks = select(Tenant.id).where(Tenant.is_active.is_(True))
        if not get_settings().control_tests_include_sandboxes:
            banks = banks.where(Tenant.is_sandbox.is_(False))
        for tenant_id in list(session.execute(banks).scalars()):
            try:
                bind_session_to_tenant(session, tenant_id)
                ran = control_results_service.run_checks(session, tenant_id, chosen, period=control_results_service.previous_day())
                session.rollback()  # the checks only read
                population = {c.key for c in chosen if c.kind == "population"}
                controls_service.record_changes(tenant_id, [c for c, _o in ran], record_results="schedule")
                if population:
                    bind_session_to_tenant(session, tenant_id)
                    control_results_service.record(session, tenant_id, [o for c, o in ran if c.key in population], started_by="schedule")
                    session.commit()
            except Exception:  # noqa: BLE001 -- one bank's failure never stops another's
                session.rollback()
                logger.exception("Controls %s run failed for bank %s", schedule, tenant_id)
    finally:
        session.close()


def malware_recheck_job() -> None:
    """Hourly: asks VirusTotal again about uploads it couldn't answer for when they arrived
    (services/malware_service.py::recheck_due)."""
    from openclear.services import malware_service

    session = get_session_factory()()
    try:
        malware_service.recheck_due(session)
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("Malware re-check failed")
    finally:
        session.close()


def housekeeping_job() -> None:
    """Daily clean-up of operational rows (services/housekeeping_service.py): finished
    jobs, sent notifications, old bulk-upload row results and expired export archives; then
    each bank's rules for inactive access (services/inactivity_service.py); then customers whose
    last service day or retention has passed (services/customer_offboarding_service.py)."""
    from openclear.services import customer_offboarding_service, housekeeping_service, inactivity_service

    session = get_session_factory()()
    try:
        housekeeping_service.run(session)
        try:
            inactivity_service.sweep_all(session)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("Deactivating and removing inactive access failed")
        try:
            customer_offboarding_service.sweep_all(session)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("Ending customers' service and deleting their data failed")
    finally:
        session.close()


def anomaly_capture_job() -> None:
    """Daily: the synthetic anomaly-capture test for every bank (services/mrm_service.py),
    each in its own transaction so one bank's failure doesn't stop the rest."""
    from openclear.services import mrm_service

    session = get_session_factory()()
    try:
        tenant_ids = list(session.execute(select(Tenant.id).where(Tenant.is_active.is_(True))).scalars())
        for tenant_id in tenant_ids:
            bind_session_to_tenant(session, tenant_id)
            try:
                run = mrm_service.anomaly_capture(session, tenant_id, user_id=None, channel="api")  # no actor: the schedule
                session.commit()
                if not run.passed:
                    logger.warning("Anomaly capture failed for tenant %s: %s", tenant_id, run.summary)
            except Exception:  # noqa: BLE001 -- one bank's failure mustn't stop the rest
                session.rollback()
                logger.exception("Anomaly capture errored for tenant %s", tenant_id)
    finally:
        session.close()


def verify_action_logs_job() -> None:
    """Daily: verify every bank's action log (services/long_runs.py), so the Controls page
    can show a recent result without walking the whole log on each load."""
    from openclear.db.session import get_session_factory
    from openclear.services import long_runs, platform_log_service

    long_runs.verify_all_banks_job()
    session = get_session_factory()()  # bound to no bank: the provider's log (DUAL_CONTROL_PLAN.md DC6)
    try:
        platform_log_service.verify_and_keep(session)
        session.commit()
    finally:
        session.close()
