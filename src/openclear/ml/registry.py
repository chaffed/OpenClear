# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import hashlib
import hmac
import io
import uuid
from datetime import datetime, timezone
from typing import Any

import joblib
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.domain.ml_model import MlModel, MlModelStatus
from openclear.ml.model import ScoringModel


class ArtifactIntegrityError(Exception):
    """A model file doesn't match the SHA-256 recorded for it, or none was recorded. It's
    never unpickled: unpickling runs code, so a swapped file would run code in the app."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactStore:
    """Model files, wherever storage.py keeps them (local disk, or object storage shared by
    every instance).

    Artifacts are joblib (pickle) files, so loading one can run arbitrary code. Every
    file's SHA-256 is recorded on its ml_model row when it's written, and load_model()
    refuses a file that doesn't match. The database is already what decides which file
    to load, so this adds no new secret: someone who can write to the artifact directory
    (or a shared volume) but not the database can no longer get code run. Hashing and
    unpickling use the same in-memory bytes, so the file can't be swapped in between."""

    def save(self, model: ScoringModel, key: str) -> tuple[str, str]:
        """Writes the model (storage.py) and returns (reference, sha256 of exactly the
        bytes written)."""
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        data = buffer.getvalue()
        return storage.put("ml_artifacts", f"{key}.joblib", data), _sha256(data)

    def _verified_bytes(self, path: str, expected_sha256: str | None) -> bytes:
        if not expected_sha256:
            raise ArtifactIntegrityError(
                f"No fingerprint is recorded for model file {path}, so it won't be loaded. Retrain the model to replace it."
            )
        data = storage.read(path)
        if not hmac.compare_digest(_sha256(data), expected_sha256):
            raise ArtifactIntegrityError(f"Model file {path} has changed since it was saved, so it won't be loaded.")
        return data

    def load_model(self, row: MlModel) -> ScoringModel:
        return joblib.load(io.BytesIO(self._verified_bytes(row.artifact_path, row.artifact_sha256)))

    def copy_model(self, row: MlModel, key: str) -> tuple[str, str]:
        """Copies a verified artifact to a new key and returns (reference, sha256), the
        sha256 unchanged."""
        data = self._verified_bytes(row.artifact_path, row.artifact_sha256)
        return storage.put("ml_artifacts", f"{key}.joblib", data), row.artifact_sha256


def _slot_filter(stmt, *, tenant_id: uuid.UUID | None, customer_id: uuid.UUID | None):
    """A model "slot" — at most one ACTIVE model each (see domain/ml_model.py):
    customer_id given → that customer's model (customer ids are globally unique, so its
    bank doesn't need matching too); otherwise tenant_id given → that bank's bank-only
    model; neither → the shared network model."""
    if customer_id is not None:
        return stmt.where(MlModel.customer_id == customer_id)
    return stmt.where(MlModel.customer_id.is_(None), MlModel.tenant_id == tenant_id)


def get_active_model_row(
    session: Session, network_code: str, customer_id: uuid.UUID | None = None, *, tenant_id: uuid.UUID | None = None
) -> MlModel | None:
    stmt = select(MlModel).where(MlModel.network_code == network_code, MlModel.status == MlModelStatus.ACTIVE)
    return session.execute(_slot_filter(stmt, tenant_id=tenant_id, customer_id=customer_id)).scalars().first()


def list_slot_models(
    session: Session, network_code: str, *, tenant_id: uuid.UUID | None = None, customer_id: uuid.UUID | None = None
) -> list[MlModel]:
    """Every model (any status) in one slot, newest first — the history an admin page shows."""
    stmt = select(MlModel).where(MlModel.network_code == network_code).order_by(MlModel.created_at.desc())
    return list(session.execute(_slot_filter(stmt, tenant_id=tenant_id, customer_id=customer_id)).scalars().all())


def create_model_row(
    session: Session,
    *,
    network_code: str,
    version: str,
    algorithm: str,
    artifact_path: str,
    trained_from_decision_count: int,
    metrics_json: dict[str, Any],
    status: MlModelStatus,
    customer_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    artifact_sha256: str | None = None,
) -> MlModel:
    """`artifact_sha256` should be the digest ArtifactStore.save() returned. Without it the
    row can't be loaded (see ArtifactStore), which is right for rows with no file yet."""
    row = MlModel(
        network_code=network_code,
        customer_id=customer_id,
        tenant_id=tenant_id,
        version=version,
        algorithm=algorithm,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        trained_from_decision_count=trained_from_decision_count,
        metrics_json=metrics_json,
        status=status,
        activated_at=datetime.now(timezone.utc) if status == MlModelStatus.ACTIVE else None,
    )
    session.add(row)
    session.flush()
    return row


def activate_model(
    session: Session,
    model_id: uuid.UUID,
    *,
    expected_customer_id: uuid.UUID | None,
    expected_tenant_id: uuid.UUID | None = None,
) -> MlModel:
    """`expected_customer_id` (required) and `expected_tenant_id` name the slot the caller
    means to activate a model in; a model from any other slot is rejected with the same
    "not found" as an unknown id, so this never reveals that it exists. In particular a
    bank can only activate its own bank-only models, and only a caller passing neither
    (the platform operator) can activate a shared-model row."""
    model = session.get(MlModel, model_id)
    if model is None:
        raise ValueError(f"No ml_model with id={model_id}")
    if model.customer_id != expected_customer_id:
        # A stale/unrelated/another-customer's model must never be swappable into a
        # scope it wasn't trained for — keyword-only, no default, so every caller states
        # which scope it expects rather than silently skipping the check.
        raise ValueError(f"No ml_model with id={model_id}")
    if expected_customer_id is None and model.tenant_id != expected_tenant_id:
        raise ValueError(f"No ml_model with id={model_id}")

    # Only retires the previous active row in this SAME slot — shared, bank, and customer
    # models are independent and must never retire each other.
    previous_active = get_active_model_row(session, model.network_code, model.customer_id, tenant_id=model.tenant_id)
    if previous_active is not None and previous_active.id != model.id:
        previous_active.status = MlModelStatus.RETIRED

    model.status = MlModelStatus.ACTIVE
    model.activated_at = datetime.now(timezone.utc)
    session.flush()
    return model
