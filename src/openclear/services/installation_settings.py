# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The installation's security-relevant settings, as a digest (DUAL_CONTROL_PLAN.md DC-D14).
They're environment settings, changed outside OpenClear under the provider's own change
management; this makes a change visible. The "Installation settings unchanged" check compares
them with what a platform operator last acknowledged: a difference is a WARN (and a deviation)
until acknowledged in the operator view, which goes in the provider's action log. Each setting
is kept as its own digest, never its value, so no secret is stored or shown."""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.installation_setting_digest import InstallationSettingDigest

# The settings that decide how safe the installation is, by name.
WATCHED = (
    "environment", "webauthn_origin", "webauthn_rp_id", "public_base_url", "client_ip_header", "trusted_proxy_cidrs", "edge_secret",
    "edge_secret_previous", "edge_secret_header", "jwt_public_key_path", "file_signing_public_key_path", "audit_log_signing_public_key_path",
    "wsud_signing_public_key_path", "sso_encryption_key", "jwt_access_token_expire_minutes", "jwt_refresh_token_expire_minutes",
    "login_max_failed_attempts", "login_lockout_minutes", "hsts_max_age_seconds", "siem_kind", "siem_host", "siem_url",
    "host_cuecs_attested_by", "enable_control_test_scheduler", "enable_housekeeping_scheduler", "control_tests_include_sandboxes",
    "release_feed_url", "soc2_mapping_path", "worker_mode",
)


def _fingerprint(name: str, value) -> str:
    if name.endswith("_key_path") and value:
        try:
            with open(value, "rb") as f:
                value = f.read()  # the key's own content, so swapping the file shows
        except OSError:
            value = f"unreadable:{value}"
    return hashlib.sha256(repr(value).encode() if not isinstance(value, bytes) else value).hexdigest()[:16]


def current() -> dict[str, str]:
    settings = get_settings()
    return {name: _fingerprint(name, getattr(settings, name, None)) for name in WATCHED}


def digest(values: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{k}={v};" for k, v in sorted(values.items())).encode()).hexdigest()


def acknowledged(session: Session) -> InstallationSettingDigest | None:
    return session.execute(select(InstallationSettingDigest).order_by(InstallationSettingDigest.acknowledged_at.desc()).limit(1)).scalars().first()


def changed(session: Session) -> tuple[list[str], InstallationSettingDigest | None]:
    """The watched settings that differ from what was last acknowledged; the first time, the
    baseline is kept and nothing has changed."""
    now = current()
    row = acknowledged(session)
    if row is None:
        row = InstallationSettingDigest(digest=digest(now), settings=now, acknowledged_at=datetime.now(timezone.utc))
        session.add(row)
        session.flush()
        return [], row
    if row.digest == digest(now):
        return [], row
    return sorted(k for k in set(now) | set(row.settings or {}) if now.get(k) != (row.settings or {}).get(k)), row


def acknowledge(session: Session, *, user_id) -> list[str]:
    """A platform operator accepts the installation's settings as they are now. The caller commits."""
    from openclear.services import platform_log_service

    names, row = changed(session)
    now = current()
    session.add(InstallationSettingDigest(digest=digest(now), settings=now, acknowledged_at=datetime.now(timezone.utc),
                                          acknowledged_by_user_id=user_id))
    platform_log_service.record(session, actor_user_id=user_id, action="platform.settings.acknowledged",
                                summary="Acknowledged the installation's settings" + (f": {', '.join(names)} changed" if names else ""),
                                metadata={"changed": names})
    session.flush()
    return names
