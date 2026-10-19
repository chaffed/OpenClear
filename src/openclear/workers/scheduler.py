# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from collections.abc import Callable
from datetime import timedelta
from functools import partial

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from openclear.config import get_settings
from openclear.workers.leader_lock import run_exclusively
from openclear.workers.tasks import (
    anomaly_capture_job,
    controls_nightly_job,
    controls_watch_job,
    verify_action_logs_job,
    demo_reset_job,
    dropbox_import_job,
    housekeeping_job,
    malware_recheck_job,
    notification_dispatch_job,
    retrain_job,
    security_event_delivery_job,
    sweep_expired_dispositions_job,
    decision_outbox_job,
)

_scheduler: BackgroundScheduler | None = None


def _jobs(settings) -> list[tuple[str, Callable[[], None], object, timedelta]]:
    """The jobs this installation's settings turn on: (id, job, trigger, how often it runs)."""
    day, hour = timedelta(days=1), timedelta(hours=1)
    jobs = []
    if settings.enable_ml_scheduler:
        jobs.append(("ml_retrain_job", retrain_job, CronTrigger(hour=settings.ml_retrain_cron_hour, minute=0), day))
    if settings.auto_import_enabled:
        jobs.append(("dropbox_import_job", dropbox_import_job, IntervalTrigger(seconds=settings.auto_import_interval_seconds),
                     timedelta(seconds=settings.auto_import_interval_seconds)))
    if settings.notifications_enabled:
        jobs.append(("notification_dispatch_job", notification_dispatch_job, IntervalTrigger(seconds=settings.notification_dispatch_interval_seconds),
                     timedelta(seconds=settings.notification_dispatch_interval_seconds)))
    if settings.enable_disposition_scheduler:
        jobs.append(("sweep_expired_dispositions_job", sweep_expired_dispositions_job,
                     IntervalTrigger(seconds=settings.disposition_sweep_interval_seconds), timedelta(seconds=settings.disposition_sweep_interval_seconds)))
    if settings.enable_decision_outbox_scheduler:
        jobs.append(("decision_outbox_job", decision_outbox_job, IntervalTrigger(seconds=settings.decision_outbox_tick_seconds),
                     timedelta(seconds=settings.decision_outbox_tick_seconds)))
    if settings.enable_security_event_delivery_scheduler:
        jobs.append(("security_event_delivery_job", security_event_delivery_job,
                     IntervalTrigger(seconds=settings.security_event_delivery_tick_seconds), timedelta(seconds=settings.security_event_delivery_tick_seconds)))
    if settings.enable_housekeeping_scheduler:
        jobs.append(("housekeeping_job", housekeeping_job, CronTrigger(hour=settings.housekeeping_cron_hour, minute=30), day))
    if settings.enable_control_test_scheduler:
        jobs.append(("anomaly_capture_job", anomaly_capture_job, CronTrigger(hour=settings.control_test_cron_hour, minute=15), day))
        jobs.append(("verify_action_logs_job", verify_action_logs_job, CronTrigger(hour=settings.control_test_cron_hour, minute=45), day))
        jobs.append(("controls_watch_job", controls_watch_job, CronTrigger(minute=5), hour))
        jobs.append(("controls_nightly_job", controls_nightly_job, CronTrigger(hour=settings.control_test_cron_hour, minute=55), day))
    if settings.malware_virustotal_api_key:
        jobs.append(("malware_recheck_job", malware_recheck_job, IntervalTrigger(hours=1), hour))
    if demo_reset_enabled(settings):
        jobs.append(("demo_reset_job", demo_reset_job, IntervalTrigger(minutes=settings.demo_tenant_reset_interval_minutes),
                     timedelta(minutes=settings.demo_tenant_reset_interval_minutes)))
    return jobs


def expected_jobs() -> dict[str, timedelta]:
    """Each scheduled job this installation's settings turn on, and how often (for the
    scheduled_jobs check; whichever process runs the scheduler records into the same table)."""
    return {job_id: every for job_id, _job, _trigger, every in _jobs(get_settings())}


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler()
    for job_id, job, trigger, _every in _jobs(get_settings()):
        _add_job(_scheduler, job_id, job, trigger)
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
