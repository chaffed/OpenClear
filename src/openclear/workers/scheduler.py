# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from collections.abc import Callable
from functools import partial

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from openclear.config import get_settings
from openclear.workers.leader_lock import run_exclusively
from openclear.workers.tasks import (
    anomaly_capture_job,
    controls_watch_job,
    verify_action_logs_job,
    demo_reset_job,
    dropbox_import_job,
    housekeeping_job,
    notification_dispatch_job,
    retrain_job,
    security_event_delivery_job,
    sweep_expired_dispositions_job,
    decision_outbox_job,
)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    _scheduler = BackgroundScheduler()
    if settings.enable_ml_scheduler:
        _add_job(_scheduler, "ml_retrain_job", retrain_job, CronTrigger(hour=settings.ml_retrain_cron_hour, minute=0))
    if settings.auto_import_enabled:
        _add_job(_scheduler, "dropbox_import_job", dropbox_import_job, IntervalTrigger(seconds=settings.auto_import_interval_seconds))
    if settings.notifications_enabled:
        _add_job(_scheduler, "notification_dispatch_job", notification_dispatch_job, IntervalTrigger(seconds=settings.notification_dispatch_interval_seconds))
    if settings.enable_disposition_scheduler:
        _add_job(_scheduler, "sweep_expired_dispositions_job", sweep_expired_dispositions_job, IntervalTrigger(seconds=settings.disposition_sweep_interval_seconds))
    if settings.enable_decision_outbox_scheduler:
        _add_job(_scheduler, "decision_outbox_job", decision_outbox_job, IntervalTrigger(seconds=settings.decision_outbox_tick_seconds))
    if settings.enable_security_event_delivery_scheduler:
        _add_job(_scheduler, "security_event_delivery_job", security_event_delivery_job,
                 IntervalTrigger(seconds=settings.security_event_delivery_tick_seconds))
    if settings.enable_housekeeping_scheduler:
        _add_job(_scheduler, "housekeeping_job", housekeeping_job, CronTrigger(hour=settings.housekeeping_cron_hour, minute=30))
    if settings.enable_control_test_scheduler:
        _add_job(_scheduler, "anomaly_capture_job", anomaly_capture_job, CronTrigger(hour=settings.control_test_cron_hour, minute=15))
        _add_job(_scheduler, "verify_action_logs_job", verify_action_logs_job, CronTrigger(hour=settings.control_test_cron_hour, minute=45))
        _add_job(_scheduler, "controls_watch_job", controls_watch_job, CronTrigger(minute=5))
    if demo_reset_enabled(settings):
        _add_job(_scheduler, "demo_reset_job", demo_reset_job, IntervalTrigger(minutes=settings.demo_tenant_reset_interval_minutes))
    _scheduler.start()
    return _scheduler


def _add_job(scheduler: BackgroundScheduler, job_id: str, job: Callable[[], None], trigger) -> None:
    # run_exclusively: with several instances on one Postgres database, only one of them
    # runs each tick of a job (see workers/leader_lock.py).
    scheduler.add_job(partial(run_exclusively, job_id, job), trigger, id=job_id, replace_existing=True)


def demo_reset_enabled(settings) -> bool:
    return settings.demo_tenant_enabled and settings.demo_tenant_reset_interval_minutes > 0


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
