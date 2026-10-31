# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# The checked-in, publicly-known key pair used for local dev and the test suite (see
# dev_keys/README.md) — every *_private_key_path/*_public_key_path setting below
# defaults to a file under here. assert_production_safe() below refuses to start the
# app with any of these defaults still in place when environment="production".
_DEV_KEYS_DIR = "dev_keys"

# The project was called PosPay before 1.6, and every setting was POSPAY_*. Those names
# still work, so existing deployments (and their secrets) keep running unchanged; when
# both are set, OPENCLEAR_* wins.
LEGACY_POSPAY_ENV_PREFIX = "POSPAY_"
LEGACY_POSPAY_SQLITE_FILE = "pospay.db"


def _default_database_url() -> str:
    # A local install from before the rename keeps using its existing database file.
    if Path(LEGACY_POSPAY_SQLITE_FILE).exists() and not Path("openclear.db").exists():
        return f"sqlite:///./{LEGACY_POSPAY_SQLITE_FILE}"
    return "sqlite:///./openclear.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPENCLEAR_", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: OPENCLEAR_* (environment, then .env) before legacy POSPAY_*.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            EnvSettingsSource(settings_cls, env_prefix=LEGACY_POSPAY_ENV_PREFIX),
            DotEnvSettingsSource(settings_cls, env_file=".env", env_prefix=LEGACY_POSPAY_ENV_PREFIX),
            file_secret_settings,
        )

    # Fail-closed on purpose, unlike every other setting here: an operator who sets
    # nothing gets the STRICT behavior (assert_production_safe enforced), not the lax
    # one. Local dev (scripts/launcher.py) and the test suite (tests/conftest.py) both
    # explicitly set OPENCLEAR_ENVIRONMENT=development/test before this is ever read.
    environment: Literal["development", "production"] = "production"

    database_url: str = Field(default_factory=_default_database_url)
    # Connections per process (db/session.py; ignored for SQLite). Each web process and
    # each openclear-worker opens up to db_pool_size + db_max_overflow, so the database
    # must allow at least that times the number of processes: see README, "Running more
    # than one instance".
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # JWT signing (auth/security.py) — ECDSA P-256 key pair, not a shared secret, so it
    # can't be "guessed" the way a hardcoded string could be. Generate your own with
    # scripts/generate_keys.py (see README.md's "Signing keys" section) before deploying;
    # these defaults point at the checked-in dev/test key pair only.
    jwt_private_key_path: str = f"{_DEV_KEYS_DIR}/jwt_private.pem"
    jwt_public_key_path: str = f"{_DEV_KEYS_DIR}/jwt_public.pem"
    jwt_algorithm: str = "ES256"
    # Separate key pair from JWT signing on purpose (bulk_import/signing.py) — a leaked
    # file-signing key shouldn't also let someone forge auth tokens, or vice versa.
    file_signing_private_key_path: str = f"{_DEV_KEYS_DIR}/file_signing_private.pem"
    file_signing_public_key_path: str = f"{_DEV_KEYS_DIR}/file_signing_public.pem"
    # Also a separate key pair from file signing — a distinct key per signed artifact
    # type, same key-separation reasoning (services/audit_log_service.py).
    audit_log_signing_private_key_path: str = f"{_DEV_KEYS_DIR}/audit_log_signing_private.pem"
    audit_log_signing_public_key_path: str = f"{_DEV_KEYS_DIR}/audit_log_signing_public.pem"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 60 * 24 * 7
    mfa_pending_token_expire_minutes: int = 5  # short-lived: only enough time to complete the WebAuthn ceremony

    # Account lockout (auth/login_service.py::authenticate_password) — global, not
    # per-tenant, same as every other login-security setting here; the two token expiry
    # settings above ARE separately overridable per-tenant (Tenant.access_token_expire_
    # minutes/refresh_token_expire_minutes, see services/tenant_service.py).
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15
    # Breached passwords (auth/breached_passwords.py; ASVS 5.0 6.2.4, 6.2.12): the bundled list
    # of the 100,000 most common is always checked. breached_password_file adds a bigger list
    # in Have I Been Pwned's download format (sorted "SHA1:count" lines, searched in place, so
    # even the full download needs no loading), for installations that can't reach the
    # internet. breached_password_hibp asks HIBP's range API (k-anonymity: only the first five
    # characters of the hash leave the server); if it can't be reached, the lists decide.
    breached_password_file: str | None = None
    breached_password_hibp: bool = False
    breached_password_hibp_timeout_seconds: float = 3.0
    # Malware scanning of uploads (services/malware_service.py; OWASP_PLAN.md OW-D34), each
    # optional; the bank's own malware detection is a CUEC either way. malware_clamd_address is
    # clamd's "tcp://host:3310" or "unix:///path/clamd.sock"; every upload is streamed to it, and
    # if it can't answer the upload is refused (or accepted, with "accept"). With
    # malware_virustotal_api_key, each upload's SHA-256 is looked up (never the file: ADR 0007);
    # if VirusTotal can't answer, the upload is accepted and the hash checked again later for
    # malware_recheck_days (or refused, with "refuse").
    malware_clamd_address: str | None = None
    malware_clamd_timeout_seconds: float = 30.0
    malware_clamd_on_error: str = "refuse"
    malware_virustotal_api_key: str | None = None
    malware_virustotal_timeout_seconds: float = 5.0
    malware_virustotal_on_error: str = "accept"
    malware_recheck_days: int = 7
    # How long the link an administrator's password reset emails stays usable
    # (services/password_reset_service.py).
    password_reset_link_hours: int = 24
    # Adding or removing a security key needs a sign-in this recent (auth/deps.py::
    # recently_signed_in; ASVS 5.0 7.5.1); otherwise the person signs in again first.
    recent_sign_in_minutes: int = 10

    # WebAuthn/FIDO2 — rp_id must be a domain the browser considers the current origin's
    # registrable domain (no scheme/port); origin must be the exact scheme+host(+port).
    # Defaults are dev-only (localhost); set both explicitly for any real deployment.
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "OpenClear"
    webauthn_origin: str = "http://localhost:8000"

    ocr_provider: str = "tesseract"
    # Prevents a pathological/adversarial image from hanging a background-task slot
    # indefinitely — pytesseract raises a plain RuntimeError on timeout, already caught
    # by networks/check/ocr_processing.py's existing broad exception handler.
    ocr_timeout_seconds: int = 30
    check_image_storage_dir: str = "./data/check_images"
    tenant_asset_storage_dir: str = "./data/tenant_assets"
    bulk_upload_storage_dir: str = "./data/bulk_uploads"
    data_export_storage_dir: str = "./data/exports"
    audit_archive_storage_dir: str = "./data/audit_archives"
    decision_file_storage_dir: str = "./data/decision_files"
    # Where the host's backup job leaves its manifest (latest.json) and a SQLite installation its nightly
    # copy (CONTINUITY_PLAN.md BC-D2, BC-D8); RUNBOOK.md "Backups" has the manifest's shape.
    backup_manifest_storage_dir: str = "./data/backups"
    backup_max_age_hours: int = 26  # the Controls page warns when the last backup is older
    # Maintenance mode (CONTINUITY_PLAN.md BC-D4): sign-in and reading work, every write answers 503 except the data
    # export, the cut-off sweep pauses (and the pause counts as a gap when it ends), and every page carries the banner.
    maintenance_mode: bool = False
    sqlite_backup_keep_days: int = 14
    # Where new files are written (storage.py): "local" (the directories above) or "s3",
    # an S3-compatible bucket shared by every instance (pip install 'openclear[s3]').
    # s3_endpoint_url is for MinIO, Cloudflare R2 and the like; leave it empty for AWS.
    # With the key settings empty, boto3 finds credentials itself (environment, role).
    storage_backend: str = "local"  # "local", "s3", "azure", or one a plugin provides
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # "azure": Azure Blob Storage (pip install 'openclear[azure]'). Either a connection string,
    # or the account's URL (https://<account>.blob.core.windows.net) with credentials found by
    # azure-identity (a managed identity, the environment, or the Azure CLI).
    azure_storage_connection_string: str = ""
    azure_storage_account_url: str = ""
    azure_storage_container: str = ""
    azure_storage_prefix: str = ""

    # Resource-exhaustion guards (main.py's request middleware, bulk_import/zip_import.py).
    max_request_body_bytes: int = 50 * 1024 * 1024
    # Decompressed zip content can reasonably exceed the wire size of the compressed
    # upload itself, hence a separate, larger cap than max_request_body_bytes.
    max_zip_uncompressed_bytes: int = 200 * 1024 * 1024

    # Reverse proxy / WAF trust (web/client_ip.py) — 0 (default) means every request's
    # client IP is taken straight from the TCP connection, never from a client-supplied
    # header. Deploying behind a reverse proxy/WAF means every direct-connection IP is
    # actually the proxy's own address, which would make IP-based rate limiting either
    # meaningless (one shared bucket for all real visitors) or, if a header were trusted
    # blindly instead, trivially spoofable — set this to the number of proxy hops in
    # front of the app (usually 1) to correctly trust exactly that many entries from the
    # right end of X-Forwarded-For instead.
    trusted_proxy_count: int = 0
    # Behind an edge network (Cloudflare, Akamai) that sends the caller's address in its own
    # header (CF-Connecting-IP, True-Client-IP): name the header here and list the edge's
    # address ranges in trusted_proxy_cidrs (comma-separated CIDRs). The header is trusted
    # only when the connection comes from those ranges (web/request_context.py; WAF_PLAN.md W1).
    client_ip_header: str | None = None
    trusted_proxy_cidrs: str = ""
    # Origin lockdown (WAF_PLAN.md W2): with edge_secret set, only requests carrying it in
    # edge_secret_header are let in (apart from /health). The edge adds the header; set
    # edge_secret_previous too while rotating. edge_attestation records any other lockdown
    # (a Cloudflare Tunnel, mTLS from the edge, a firewall allowing only the edge), which
    # OpenClear can't see itself; the Controls page shows it.
    edge_secret: str | None = None
    edge_secret_previous: str | None = None
    edge_secret_header: str = "X-OpenClear-Edge-Secret"
    edge_attestation: str | None = None

    # App-level rate limiting (web/rate_limit.py) — in-memory and per-process, matching
    # scripts/launcher.py's single uvicorn process with no workers=N; a future multi-
    # worker deployment would need to know each worker enforces its own counters, making
    # the effective limit `rate_limit_per_minute * worker_count`. Global default applies
    # to every request via main.py's middleware; markdown_preview_rate_limit_per_minute
    # is a stricter limit on the one endpoint reachable by anyone merely logged in (no
    # CSRF, no other permission check) and CPU-costly per call.
    rate_limit_per_minute: int = 120
    # Signed-in people and API clients are limited per person or client rather than per IP
    # address, so a bank behind one NAT address, an edge network, or one core system isn't
    # throttled as a single caller (WAF_PLAN.md W1).
    rate_limit_per_principal_per_minute: int = 600
    markdown_preview_rate_limit_per_minute: int = 30
    # Password sign-ins (web, API, platform) per caller address per minute, on top of the lockout
    # per account (FULL_REVIEW_PLAN.md FR-D7): one address guessing across many accounts is slowed
    # here; one account guessed from many addresses is stopped by the lockout.
    login_attempts_per_minute: int = 20
    # "memory" counts per process (above). "redis" shares the counts between every
    # instance (pip install 'openclear[redis]'); if Redis can't be reached, requests are
    # allowed and the error logged, so an outage of the limiter isn't an outage of the app.
    # OAuth 2.0 client credentials for the bank's systems (services/api_client_service.py):
    # how long an access token from /api/v1/oauth/token lasts, and how many token
    # requests one address may make a minute.
    api_client_token_minutes: int = 30
    # How long an Idempotency-Key's stored response is kept for replays (api/idempotency.py);
    # housekeeping removes older ones.
    idempotency_key_hours: int = 24
    # Plugins (plugins/__init__.py): the installed packages allowed to load, by entry point
    # name, comma-separated. Installing a package alone activates nothing.
    plugins: str = ""
    # Plugins the app won't start without (allowed too, so no need to list them twice).
    plugins_required: str = ""
    # Webhooks (services/webhook_service.py): the receiver must answer within this many
    # seconds. Webhook URLs must be public https unless webhook_allow_private_hosts is on,
    # for a single-bank install whose receivers are on its own network. Leave it off when
    # several banks share an installation: one bank could otherwise aim requests at the
    # platform's own internal addresses.
    webhook_timeout_seconds: int = 10
    webhook_allow_private_hosts: bool = False
    oauth_token_rate_limit_per_minute: int = 30
    # Vendor sandboxes (services/sandbox_service.py, SDK_PLAN.md P4): only on an installation
    # run as a public sandbox. Anyone can create a seeded sandbox bank at /ui/sandbox; it's
    # wiped after sandbox_lifetime_days. Never turn this on for an installation with real banks.
    sandbox_signup_enabled: bool = False
    sandbox_lifetime_days: int = 30
    sandbox_signups_per_hour: int = 5  # per address
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_redis_url: str = ""

    # Security response headers (web/security_headers.py) — only Strict-Transport-Security
    # needs a setting of its own; everything else there is a fixed, non-configurable policy.
    # Two years, the widely-recommended HSTS preload-eligible minimum. Only ever sent when
    # is_https_deployment() is true (see that function's docstring).
    hsts_max_age_seconds: int = 63_072_000

    check_stale_date_default_days: int = 180
    payee_match_fuzzy_threshold: float = 85.0

    # How long a data export (services/data_export_service.py) may run before it fails.
    # Exports run as background jobs and stream to disk, so there's no row limit; this
    # is the only bound. Tenant.data_export_timeout_seconds overrides it per bank (None
    # there means "use this"). Was 300 while exports ran inside the web process.
    data_export_timeout_seconds: int = 3600

    # The camt.086 (ISO 20022 Bank Services Billing) message version the billing export
    # declares (services/billing/exports.py). Match it to what the receiving analysis
    # system or corporate client expects, and validate against its XSD.
    billing_camt086_version: str = "camt.086.001.04"
    # Who the platform operator's invoices to banks come from (the camt.086 sender).
    platform_operator_name: str = "OpenClear platform operator"
    platform_operator_id: str = "OPENCLEAR-PLATFORM"

    ml_min_new_decisions_for_retrain: int = 20
    # Minimum time between two retrains of the same bank's model for a network —
    # closes off repeatedly hammering the on-demand retrain endpoint into a self-service
    # compute DoS (ml/train.py::train_model). The nightly scheduled job is already gated
    # by ml_min_new_decisions_for_retrain and won't normally trip this too.
    ml_retrain_cooldown_seconds: int = 60
    # What one training run reads (SCALE_PLAN.md S5): decisions from the last
    # ml_training_window_days (0 = all history), and at most ml_training_max_rows of the
    # most recent of those.
    ml_training_window_days: int = 365
    ml_training_max_rows: int = 250_000
    # Loaded models each process keeps in memory (one per model slot that has scored
    # something): the least recently used is dropped past this.
    ml_model_cache_size: int = 256
    # Which scoring model training builds: "logistic_regression" (built in), or one a
    # plugin provides (plugins/__init__.py).
    ml_scoring_model: str = "logistic_regression"
    # A retrained model replaces the bank's active model only once the bank has at least
    # this many of its own labeled decisions AND the new model beats the active one on the
    # same recent held-out decisions (ml/train.py).
    ml_bank_model_min_decisions: int = 200
    ml_artifact_dir: str = "./ml_artifacts"  # NOT inside src/openclear/ — that's installed package code, not a place for runtime output
    enable_ml_scheduler: bool = False  # opt-in: off by default so tests/local dev don't spawn a background thread
    ml_retrain_cron_hour: int = 2  # nightly at 2am when enabled

    # Auto-import dropbox (services/dropbox_import_service.py) — a directory tree an
    # external system (a core-banking export, an SFTP drop) can land files into for
    # unattended import, isolated by tenant slug (and optionally customer number)
    # subdirectory — see that module for the exact layout.
    auto_import_dropbox_dir: str = "./data/auto_import_dropbox"
    auto_import_enabled: bool = False  # opt-in: off by default, same reasoning as enable_ml_scheduler
    auto_import_interval_seconds: int = 300  # how often the in-app scheduler polls, when enabled
    # A dropped file is only processed once its mtime is at least this old — a simple,
    # sender-agnostic guard against reading a file an external system is still mid-write
    # on, without requiring that system to adopt a write-then-atomic-rename convention.
    auto_import_min_file_age_seconds: int = 30
    # Same resource-exhaustion-guard reasoning as max_request_body_bytes, applied to a
    # dropped file read directly off disk rather than an HTTP upload.
    auto_import_max_file_bytes: int = 50 * 1024 * 1024

    # Notifications (services/notification_service.py queues, workers/tasks.py::
    # notification_dispatch_job drains) — email/SMS are never sent inline in a request,
    # so a slow/unreachable provider never blocks the action that triggered it.
    notifications_enabled: bool = False  # opt-in: off by default, same reasoning as enable_ml_scheduler
    notification_dispatch_interval_seconds: int = 30
    notification_dispatch_batch_size: int = 200
    email_provider: str = "smtp"  # see notifications/email/factory.py

    # Default disposition (services/auto_disposition_service.py, workers/tasks.py::
    # sweep_expired_dispositions_job) — auto-decides an exception whose decision_deadline
    # has passed with no human decision, per a per-customer CustomerDispositionSetting.
    enable_disposition_scheduler: bool = False  # opt-in: off by default, same reasoning as enable_ml_scheduler
    disposition_sweep_interval_seconds: int = 300
    # How often decisions past their cut-off are made final and sent (workers/tasks.py::
    # decision_lock_job), with the disposition scheduler on. A core sees a decision at most this
    # long after its cut-off.
    decision_lock_interval_seconds: int = 60
    # The exception.deadline_approaching webhook goes out this many hours before an open
    # exception's decision deadline (checked by the same sweep).
    webhook_deadline_warning_hours: int = 4
    # Reminders before the cutoff (services/reminder_service.py; UX_PLAN.md U3): sent this
    # many minutes before each open exception's decision deadline, comma-separated, by the
    # same sweep. A bank sets its own in Settings → Reminders; this is the default for banks
    # that haven't. Empty: no reminders unless a bank turns them on.
    reminder_offsets_minutes: str = "120,30"
    # Decision outbox (services/decision_outbox_service.py, SDK_PLAN.md P3): writes each
    # bank's new decisions to a file on the bank's own schedule. This tick checks who's due.
    enable_decision_outbox_scheduler: bool = False  # opt-in, same reasoning as enable_ml_scheduler
    decision_outbox_tick_seconds: int = 60
    # Housekeeping (services/housekeeping_service.py, SCALE_PLAN.md S7): clears out
    # operational rows that otherwise grow with every request. Runs daily at
    # housekeeping_cron_hour when enabled (or call workers.tasks.housekeeping_job from
    # cron). Each period is in days; 0 keeps those rows forever. A bank's business records
    # (checks, ACH, exceptions, decisions) and its audit log are never touched here.
    enable_housekeeping_scheduler: bool = False  # opt-in, same reasoning as enable_ml_scheduler
    housekeeping_cron_hour: int = 3
    # Scheduled control testing (workers/scheduler.py): every bank's hourly checks
    # (controls_watch_job), and at control_test_cron_hour the daily anomaly-capture test
    # (services/mrm_service.py, COMPLIANCE_PLAN.md C2), action log verification, the nightly
    # and population checks over the previous day, and each bank's proposed CUEC attestations
    # once its period ends (CONTROLS_PLAN.md CT2, CT4). Without it no scheduled result is kept,
    # so automated attestations come out "not tested". Or call those jobs in workers/tasks.py
    # from cron. The Controls page warns when a bank's last anomaly-capture run is over a day old.
    enable_control_test_scheduler: bool = False  # opt-in, same reasoning as enable_ml_scheduler
    control_test_cron_hour: int = 4
    # Sandbox banks (an evaluator's, made by sign-up) are left out of scheduled control testing
    # and attestation proposals, unless this is on: the public sandbox turns it on so its banks
    # build a test history to look at. The yearly request to customers never goes to them.
    control_tests_include_sandboxes: bool = False
    # The host's software currency check (services/release_feed.py, CONTROLS_PLAN.md CT2): where
    # OpenClear's releases are listed, as GitHub's releases API gives them (a mirror inside your
    # network works too); empty turns the check off, and it then warns that it can't tell. A newer
    # release out longer than the first number of days is a WARN, longer than the second a FAIL.
    release_feed_url: str = "https://api.github.com/repos/chaffed/OpenClear/releases"
    # The SOC 2 crosswalk (compliance/crosswalks.py, CONTROLS_PLAN.md CT6): the JSON that
    # `scripts/import_crosswalks.py soc2` makes from your own copy of AICPA's TSC to NIST 800-53
    # mapping. Empty (the default): no SOC 2 column. AICPA's terms govern its use; OpenClear
    # doesn't ship it.
    soc2_mapping_path: str = ""
    software_currency_warn_days: int = 30
    software_currency_fail_days: int = 180
    retention_finished_jobs_days: int = 30
    retention_notifications_days: int = 180
    retention_bulk_row_results_days: int = 365
    # Data export archives hold a full copy of a bank's (or customer's) data, so they
    # don't stay downloadable forever: the file is deleted and the export marked expired.
    retention_data_exports_days: int = 30
    # Audit log entries older than this many days are moved, verified, into a signed
    # archive file per run (services/audit_archive_service.py). Nothing is deleted
    # without being archived first, and the chain still verifies end to end. 0 (the
    # default) keeps every entry in the table.
    audit_archive_after_days: int = 0

    # Background jobs (workers/jobs.py): OCR, data exports, dropbox scans, retraining.
    # "in_process" runs a worker thread inside the web process, so a single-process
    # install (the launcher, the Fly demo) needs nothing extra. "external" runs no worker
    # in the web process: start `openclear-worker` separately and scale it on its own.
    # "inline" runs each job right after whatever queued it (the test suite).
    worker_mode: Literal["in_process", "external", "inline"] = "in_process"
    worker_poll_seconds: float = 2.0
    job_max_attempts: int = 5
    # A running job whose worker hasn't sent a heartbeat for this long is presumed dead
    # (its process crashed or was killed) and is queued again.
    job_stale_after_seconds: int = 300
    # Bulk files are imported by a background job (services/bulk_import_service.py), which
    # commits this many rows at a time. A JSON API bulk call with more items than
    # bulk_api_sync_max_rows is imported the same way and answers 202 with an upload to
    # poll; at or under it, the call still answers with every row's result.
    bulk_import_chunk_rows: int = 1000
    bulk_api_sync_max_rows: int = 1000
    # Fallback for CustomerDispositionSetting.response_window_hours when a customer's own
    # setting leaves it unset — same nullable-override-falls-back-to-global-default shape
    # as data_export_timeout_seconds above.
    default_disposition_response_window_hours: int = 24

    # Persistent sales-demo tenant (services/demo_tenant_service.py) — a fully-seeded,
    # real tenant that resets to its pristine state the next time anyone logs into it
    # after its own (short) session window has elapsed. Off by default; a password must
    # also be set, since there's no safe hardcoded default for a real credential.
    demo_tenant_enabled: bool = False
    demo_tenant_password: str | None = None
    demo_tenant_session_minutes: int = 60
    # Besides the reset-after-idle above, the demo also resets on this fixed schedule,
    # because a visitor who stays active would otherwise keep whatever they changed in
    # place for everyone else indefinitely (see workers/tasks.py::demo_reset_job). 0 turns
    # the scheduled reset off.
    demo_tenant_reset_interval_minutes: int = 60

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_address: str = "notifications@openclear.local"
    smtp_timeout_seconds: float = 10.0
    sms_provider: str = "twilio"  # see notifications/sms/factory.py; requires pip install openclear[sms]
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    # Encrypts secrets stored in the database (SSO client secrets, the SAML signing key,
    # webhook secrets, secret plugin settings) with AES-256-GCM, through HKDF (auth/crypto.py),
    # so this stays a plain string like every other *_secret_key setting. This is
    # ENCRYPTION, not signing, so it deliberately doesn't
    # move to a key pair like jwt/file_signing/audit_log_signing above — but it still
    # needs a real random value in production, enforced below.
    sso_encryption_key: str = "dev-secret-change-me-32-bytes-minimum-for-sso"
    # Rotating the key: move the old value here and set a new sso_encryption_key. Values
    # under either decrypt, and startup re-encrypts them under the new one (auth/crypto.py).
    sso_encryption_key_previous: str | None = None
    # Re-encrypt stored secrets still under an older scheme or key at startup (auth/crypto.py).
    reencrypt_secrets_at_startup: bool = True
    # Logs (SIEM_PLAN.md S1): "text" (the default) or "json", one object per line with the
    # request's id, client IP, bank and person (openclear/logging_config.py).
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"
    # Security events (services/security_events.py) are kept this long for the pull API and,
    # later, for delivery; housekeeping removes older ones.
    security_event_retention_days: int = 30
    # Control test results and closed deviations (CONTROLS_PLAN.md CT2, CT-D10): kept seven
    # years by default, for examinations and attestations over past periods; 0 keeps them.
    control_result_retention_days: int = 2555
    # Who attests the host's CUECs (CONTROLS_PLAN.md CT-D12): "bank" when each bank hosts OpenClear
    # itself (the default), or "platform" on a shared installation, where its platform staff attest
    # them once and every bank sees those read-only.
    host_cuecs_attested_by: Literal["bank", "platform"] = "bank"
    # Security event delivery (services/siem/; SIEM_PLAN.md S3). The installation's own
    # destination, which receives every bank's events: siem_kind is "syslog", "splunk_hec"
    # or "https" (empty: none). Syslog takes host, port, transport (tls, tcp or udp; udp only
    # for a relay on the same host) and format (json, cef or leef). Splunk HEC and HTTPS take
    # a URL and a token. siem_tls_ca_file trusts a private CA (syslog over TLS, HEC, HTTPS). Each bank
    # can add its own destination in Settings; those must be public addresses unless
    # siem_allow_private_hosts is on (same reasoning as webhook_allow_private_hosts).
    siem_kind: str = ""
    siem_host: str = ""
    siem_port: int = 6514
    siem_transport: Literal["tls", "tcp", "udp"] = "tls"
    siem_format: Literal["json", "cef", "leef"] = "json"
    siem_url: str = ""
    siem_token: str = ""
    siem_splunk_index: str = ""
    siem_splunk_sourcetype: str = ""
    siem_tls_ca_file: str = ""
    siem_tls_verify: bool = True
    siem_timeout_seconds: int = 10
    siem_batch_size: int = 500
    siem_settle_seconds: int = 5
    siem_allow_private_hosts: bool = False
    enable_security_event_delivery_scheduler: bool = False  # opt-in, same reasoning as enable_ml_scheduler
    security_event_delivery_tick_seconds: int = 15
    # Long requests run as background jobs (services/long_runs.py; WAF_PLAN.md W3): a replay
    # larger than this many bytes, and an evidence export longer than this many days.
    # Smaller ones still answer at once.
    long_request_sync_bytes: int = 1_000_000
    long_request_sync_days: int = 32
    # How the host encrypts disks and the database (e.g. "AWS RDS storage encryption with
    # KMS key alias/openclear; EBS volumes encrypted"). OpenClear can't see this itself; the
    # Controls page (services/controls_service.py) shows the statement, or warns without one.
    storage_encryption_attestation: str | None = None
    # The host's recovery objectives (e.g. "RTO 4 hours, RPO 15 minutes; restored to a second
    # region") and the date of its last restore test (REGULATORY_PLAN.md RG4; the FFIEC BCM
    # booklet asks a bank to check its own objectives against its provider's). Shown on the
    # Controls page, which warns without them or once the test is over a year old.
    recovery_objectives_attestation: str | None = None
    last_restore_test: date | None = None
    # Offboarding a bank (plans/OFFBOARDING_PLAN.md): the days between telling the bank and being
    # able to delete its data, and the hours an approved deletion waits, still cancellable.
    bank_offboarding_wait_days: int = 30
    bank_deletion_delay_hours: int = 24
    # A customer's data is deleted this many days after its last service day
    # (plans/CUSTOMER_OFFBOARDING_PLAN.md CO-D1): positive pay isn't the bank's system of record.
    customer_data_retention_days: int = 90
    oidc_http_timeout_seconds: float = 10.0
    # Lets SSO connections point at http:// and private/loopback addresses (a local test
    # identity provider such as a Keycloak container on localhost). Off by default, and
    # refused outright in production by assert_production_safe below — with it on, anyone
    # who can edit an SSO connection can make this server call into its own network. See
    # auth/outbound_http.py.
    oidc_allow_private_hosts: bool = False
    # Override for the redirect_uri host when a deployment sits behind a proxy that
    # doesn't forward the original scheme/host correctly — same class of caveat as
    # webauthn_origin. None (default) derives it from the live request instead.
    public_base_url: str | None = None
    # Every bank has a decision cut-off (UI_PLAN.md UI-D13; services/cutoff_service.py): until it
    # sets its own, this one, in this time zone. Production refuses to start without it (below);
    # only the tests leave it empty, so their decisions are final when made unless a test sets one.
    default_decision_cutoff: str = "11:00"
    default_time_zone: str = "America/New_York"
    # Where this installation's source code is (AGPL-3.0 §13: anyone using it over a network is
    # offered the source of the version running). An installation running changes of its own
    # points this at its own copy. Linked from every page's footer and the license page.
    source_code_url: str = "https://github.com/chaffed/OpenClear"
    # /.well-known/security.txt (RFC 9116; OWASP_PLAN.md OW3a): how to report a vulnerability in
    # this installation. Contact is one or more URIs, comma-separated ("mailto:security@bank.example,
    # https://bank.example/security"); without one the file isn't served. Expires defaults to a
    # year from today; set a date to make it go stale on purpose if the contact isn't reviewed.
    security_txt_contact: str | None = None
    # FastAPI's interactive API pages (/docs, /redoc): None means on everywhere except production.
    api_docs_enabled: bool | None = None
    security_txt_expires: date | None = None
    security_txt_policy: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Every setting here that still equals its checked-in dev/test default is a genuine
# security hole if it ever reaches a real deployment (see SECURITY_REVIEW.md) — a
# hardcoded default field() a caller might just never override. Checked once at app
# startup (main.py::create_app), not lazily, so a misconfigured production deployment
# fails immediately and loudly rather than serving requests insecurely.
_DEFAULT_FIELDS: tuple[str, ...] = (
    "jwt_private_key_path",
    "jwt_public_key_path",
    "file_signing_private_key_path",
    "file_signing_public_key_path",
    "audit_log_signing_private_key_path",
    "audit_log_signing_public_key_path",
    "sso_encryption_key",
)

def assert_production_safe(settings: Settings) -> None:
    if settings.environment != "production":
        return
    if not (settings.default_decision_cutoff or "").strip():
        raise RuntimeError("Refusing to start with OPENCLEAR_ENVIRONMENT=production and no OPENCLEAR_DEFAULT_DECISION_CUTOFF: "
                           "every bank needs a decision cut-off (HH:MM, such as 11:00).")
    defaults = Settings.model_fields
    still_default = [name for name in _DEFAULT_FIELDS if getattr(settings, name) == defaults[name].default]
    if still_default:
        raise RuntimeError(
            "Refusing to start with OPENCLEAR_ENVIRONMENT=production while these settings "
            f"are still at their insecure checked-in dev/test defaults: {', '.join(still_default)}. "
            "Generate real ones with `python scripts/generate_keys.py` and a random "
            "OPENCLEAR_SSO_ENCRYPTION_KEY — see README.md's 'Signing keys' section."
        )

    if settings.storage_backend == "s3" and not settings.s3_bucket:
        raise RuntimeError("Refusing to start: OPENCLEAR_STORAGE_BACKEND is s3 but OPENCLEAR_S3_BUCKET isn't set.")
    if settings.storage_backend == "azure" and not (settings.azure_storage_container
                                                    and (settings.azure_storage_connection_string or settings.azure_storage_account_url)):
        raise RuntimeError("Refusing to start: OPENCLEAR_STORAGE_BACKEND is azure but OPENCLEAR_AZURE_STORAGE_CONTAINER and a "
                           "connection string or account URL aren't set.")
    if settings.rate_limit_backend == "redis" and not settings.rate_limit_redis_url:
        raise RuntimeError("Refusing to start: OPENCLEAR_RATE_LIMIT_BACKEND is redis but OPENCLEAR_RATE_LIMIT_REDIS_URL isn't set.")

    if settings.oidc_allow_private_hosts:
        raise RuntimeError(
            "Refusing to start with OPENCLEAR_ENVIRONMENT=production while "
            "OPENCLEAR_OIDC_ALLOW_PRIVATE_HOSTS is on: it lets anyone who can edit an SSO "
            "connection make this server send requests to internal addresses. It's only "
            "for local testing against an identity provider on localhost."
        )

    # Local import to avoid a cycle: ocr/factory.py (which does real provider
    # construction) imports this module, so this module can only import the small,
    # dependency-free constant from ocr/base.py, not factory.py itself.
    if not settings.siem_tls_verify:
        raise RuntimeError(
            "Refusing to start with OPENCLEAR_ENVIRONMENT=production while OPENCLEAR_SIEM_TLS_VERIFY is false: "
            "security events would go to a receiver whose certificate isn't checked. Give its CA in "
            "OPENCLEAR_SIEM_TLS_CA_FILE instead."
        )
    if settings.log_format != "json":
        logging.getLogger(__name__).warning(
            "OPENCLEAR_LOG_FORMAT is text in production: use json so log collectors can parse every field "
            "(text lines are escaped against injection, but harder to search)."
        )
    from openclear.ocr.base import UNIMPLEMENTED_PROVIDERS

    if settings.ocr_provider in UNIMPLEMENTED_PROVIDERS:
        raise RuntimeError(
            f"Refusing to start with OPENCLEAR_ENVIRONMENT=production while ocr_provider="
            f"{settings.ocr_provider!r} has no working implementation — its .extract() "
            "is a stub that raises NotImplementedError the first time a check image is "
            "uploaded (see ocr/textract_provider.py / ocr/azure_di_provider.py). Only "
            "'tesseract' is currently implemented."
        )
