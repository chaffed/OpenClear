# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Cross-tenant, read-only API keys for the usage-metering API
(api/v1/platform_usage.py) — see domain/platform_api_key.py for why this is the one
credential type in the app not scoped to a single tenant."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.platform_api_key import PlatformApiKey

_KEY_PREFIX = "openclear_platform_"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# What a platform key may be allowed to do (PlatformApiKey.scopes):
#   usage        — the usage-metering API (api/v1/platform_usage.py)
#   operations   — watching and retrying background jobs (api/v1/platform_jobs.py)
#   billing      — the operator's fee schedule and invoices to banks (api/v1/platform_billing.py)
SCOPES = ("usage", "operations", "billing")


def check_scopes(scopes: tuple[str, ...]) -> list[str]:
    unknown = set(scopes) - set(SCOPES)
    if unknown or not scopes:
        raise ValueError(f"Scopes must be a non-empty subset of {SCOPES}; got {scopes!r}")
    return sorted(set(scopes))


def mint() -> tuple[str, str]:
    """A new raw key and its hash. The raw key is shown once to whoever asked; a key made through
    a change request (DUAL_CONTROL_PLAN.md DC-D12) carries only the hash until it's approved."""
    raw_key = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw_key, _hash_key(raw_key)


def create_from_hash(session: Session, name: str, scopes: tuple[str, ...], key_hash: str) -> PlatformApiKey:
    row = PlatformApiKey(name=name, key_hash=key_hash, scopes=check_scopes(scopes))
    session.add(row)
    session.flush()
    return row


def generate_and_create(session: Session, name: str, scopes: tuple[str, ...] = ("usage",)) -> tuple[PlatformApiKey, str]:
    """Mints a new key and returns (row, raw_key) — raw_key is shown to the caller
    exactly once here; only its hash is ever persisted, so it can't be recovered later,
    only revoked and replaced with a new one. Give each integration only the scopes it
    needs: a billing system gets "usage", never "operations"."""
    raw_key, key_hash = mint()
    return create_from_hash(session, name, scopes, key_hash), raw_key


def verify(session: Session, raw_key: str) -> PlatformApiKey | None:
    """None for a missing, unknown, or revoked key -- never raises, so callers (the auth
    dependency) always get a clean pass/fail. Stamps last_used_at on success; caller
    commits."""
    row = session.execute(
        select(PlatformApiKey).where(PlatformApiKey.key_hash == _hash_key(raw_key))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    session.flush()
    return row


def revoke(session: Session, key_id: uuid.UUID) -> PlatformApiKey | None:
    row = session.get(PlatformApiKey, key_id)
    if row is None or row.revoked_at is not None:
        return None
    row.revoked_at = datetime.now(timezone.utc)
    session.flush()
    return row
