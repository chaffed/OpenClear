# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

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
    # Also its own separate key pair — signed Written Statement of Unauthorized Debit
    # attestations (services/wsud_service.py) are a distinct signed-artifact type from
    # the three above, same key-separation reasoning.
    wsud_signing_private_key_path: str = f"{_DEV_KEYS_DIR}/wsud_signing_private.pem"
    wsud_signing_public_key_path: str = f"{_DEV_KEYS_DIR}/wsud_signing_public.pem"

    # The text shown to a customer before they e-sign a Written Statement of Unauthorized
    # Debit (services/wsud_service.py) — these defaults implement the *structural*
    # elements the federal E-SIGN Act (15 U.S.C. § 7001) requires (a disclosure the
    # consumer must affirmatively consent to, a distinct signing act, tamper-evident
    # retention of exactly what was shown and signed) but have NOT been reviewed by a
    # lawyer. assert_production_safe() below refuses to start with these still at their
    # checked-in defaults when environment="production" — same "must override before
    # going live" treatment as the signing keys and sso_encryption_key above. A bank
    # deploying this should have its own counsel review and, if needed, replace this text
    # (via OPENCLEAR_WSUD_CONSENT_DISCLOSURE_TEXT / OPENCLEAR_WSUD_ATTESTATION_TEXT) before
    # relying on it. Changing this text doesn't affect any already-signed statement —
    # WsudStatement.statement_text_snapshot captures exactly what was shown and signed at
    # the time, regardless of what these settings say later.
    wsud_consent_disclosure_text: str = (
        "Before you sign electronically, please review: (1) You have the right to request a "
        "paper copy of this statement instead of signing electronically — contact your "
        "financial institution to request one. (2) You may withdraw your consent to sign "
        "electronically at any time before signing, with no effect on your ability to dispute "
        "the transaction(s) below through other means. (3) Signing electronically requires a "
        "device capable of displaying this page and retaining or printing a copy for your "
        "records; by proceeding, you confirm you can access this statement in this format. "
        "(4) This consent applies only to this specific statement, not to other documents. "
        "By checking the box below, you affirmatively consent to sign this statement "
        "electronically instead of on paper."
    )
    wsud_attestation_text: str = (
        "I certify that the ACH debit transaction(s) listed and selected below were not "
        "authorized by me, and I did not authorize the originator to debit my account for "
        "these transactions. I understand this statement may be relied upon by my financial "
        "institution to process a return of these transactions, and that I am making this "
        "statement under penalty of perjury."
    )

    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 60 * 24 * 7
    mfa_pending_token_expire_minutes: int = 5  # short-lived: only enough time to complete the WebAuthn ceremony

    # Account lockout (auth/login_service.py::authenticate_password) — global, not
    # per-tenant, same as every other login-security setting here; the two token expiry
    # settings above ARE separately overridable per-tenant (Tenant.access_token_expire_
    # minutes/refresh_token_expire_minutes, see services/tenant_service.py).
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15

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

    # App-level rate limiting (web/rate_limit.py) — in-memory and per-process, matching
    # scripts/launcher.py's single uvicorn process with no workers=N; a future multi-
    # worker deployment would need to know each worker enforces its own counters, making
    # the effective limit `rate_limit_per_minute * worker_count`. Global default applies
    # to every request via main.py's middleware; markdown_preview_rate_limit_per_minute
    # is a stricter limit on the one endpoint reachable by anyone merely logged in (no
    # CSRF, no other permission check) and CPU-costly per call.
    rate_limit_per_minute: int = 120
    markdown_preview_rate_limit_per_minute: int = 30

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
    # Minimum time between two retrains of the same (network_code, customer_id) pair —
    # closes off repeatedly hammering the on-demand retrain endpoint into a self-service
    # compute DoS (ml/train.py::train_model). The nightly scheduled job is already gated
    # by ml_min_new_decisions_for_retrain and won't normally trip this too.
    ml_retrain_cooldown_seconds: int = 60
    # Fraud-scoring model choice per bank (services/tenant_ml_service.py, FIX_PLAN.md
    # Phase 5). A newly created bank must stay on the shared model this many days before it
    # may switch to a bank-only model (banks that predate the setting aren't locked).
    ml_new_bank_private_switch_lock_days: int = 90
    # A bank-only model retrain replaces the active model only once the bank has at least
    # this many of its own labeled decisions AND the new model beats the active one on the
    # same recent held-out decisions (ml/train.py).
    ml_bank_model_min_decisions: int = 200
    # Shown, and acknowledged, whenever a bank joins the shared model (onboarding and
    # Settings). Placeholder wording: have counsel review and replace it. Unlike the WSUD
    # text this doesn't block production startup; the UI flags the placeholder instead.
    ml_shared_pool_disclosure_text: str = (
        "PLACEHOLDER — HAVE COUNSEL REVIEW. By joining the shared fraud-scoring model, your "
        "institution's pay/return decisions and the transaction features used to score them are "
        "pooled with those of other participating institutions to train a model that scores all "
        "of their exceptions. Other institutions never see your data, your name, or which "
        "decisions came from you. You can switch to a bank-only model later; your data then stops "
        "being used from the next time the shared model is retrained."
    )
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

    # Federated login (auth/oidc_service.py, services/sso_service.py) — fed through a KDF
    # into a valid Fernet key (auth/crypto.py), so this stays a plain string like every
    # other *_secret_key setting rather than a hand-generated base64 Fernet key. This is
    # ENCRYPTION (of stored SSO client secrets), not signing, so it deliberately doesn't
    # move to a key pair like jwt/file_signing/audit_log_signing above — but it still
    # needs a real random value in production, enforced below.
    sso_encryption_key: str = "dev-secret-change-me-32-bytes-minimum-for-sso"
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
    "wsud_signing_private_key_path",
    "wsud_signing_public_key_path",
    "sso_encryption_key",
)

# Separate from _DEFAULT_FIELDS above: these aren't secrets to regenerate, they're
# unreviewed placeholder legal text (see their own field comments) — a bank must have
# its own counsel review and supply real text, not run a script.
_WSUD_TEXT_FIELDS: tuple[str, ...] = (
    "wsud_consent_disclosure_text",
    "wsud_attestation_text",
)


def assert_production_safe(settings: Settings) -> None:
    if settings.environment != "production":
        return
    defaults = Settings.model_fields
    still_default = [name for name in _DEFAULT_FIELDS if getattr(settings, name) == defaults[name].default]
    if still_default:
        raise RuntimeError(
            "Refusing to start with OPENCLEAR_ENVIRONMENT=production while these settings "
            f"are still at their insecure checked-in dev/test defaults: {', '.join(still_default)}. "
            "Generate real ones with `python scripts/generate_keys.py` and a random "
            "OPENCLEAR_SSO_ENCRYPTION_KEY — see README.md's 'Signing keys' section."
        )

    still_default_wsud_text = [name for name in _WSUD_TEXT_FIELDS if getattr(settings, name) == defaults[name].default]
    if still_default_wsud_text:
        raise RuntimeError(
            "Refusing to start with OPENCLEAR_ENVIRONMENT=production while these settings "
            f"are still at their unreviewed placeholder legal text: {', '.join(still_default_wsud_text)}. "
            "This is NOT LEGAL ADVICE — have your own counsel review and supply real "
            "Written Statement of Unauthorized Debit consent/attestation text via "
            "OPENCLEAR_WSUD_CONSENT_DISCLOSURE_TEXT / OPENCLEAR_WSUD_ATTESTATION_TEXT before "
            "relying on this for a real e-signature."
        )

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
    from openclear.ocr.base import UNIMPLEMENTED_PROVIDERS

    if settings.ocr_provider in UNIMPLEMENTED_PROVIDERS:
        raise RuntimeError(
            f"Refusing to start with OPENCLEAR_ENVIRONMENT=production while ocr_provider="
            f"{settings.ocr_provider!r} has no working implementation — its .extract() "
            "is a stub that raises NotImplementedError the first time a check image is "
            "uploaded (see ocr/textract_provider.py / ocr/azure_di_provider.py). Only "
            "'tesseract' is currently implemented."
        )
