# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Written Statement of Unauthorized Debit (WSUD) e-signature — a customer-scoped
self-service attestation that one or more of the customer's own consumer-SEC-code ACH
debits, already returned, were unauthorized. See domain/wsud_statement.py.

IMPORTANT — NOT LEGAL ADVICE: get_consent_disclosure_text()/get_attestation_text()
below read config.py's wsud_consent_disclosure_text/wsud_attestation_text settings,
whose checked-in defaults are placeholder legal language implementing the *structural*
elements the federal E-SIGN Act (15 U.S.C. § 7001) requires for consumer e-signatures —
a disclosure the consumer must affirmatively consent to before signing (right to a
paper copy, how to withdraw consent, hardware/software needed), a distinct signing
act, and tamper-evident retention of exactly what was shown and signed. This has not
been reviewed by a lawyer. A bank deploying this should have its own counsel review
and supply real text via those two settings before relying on it —
config.py::assert_production_safe refuses to start at all with the defaults still in
place when environment="production"."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.keys import load_private_key, load_public_key
from openclear.config import get_settings
from openclear.domain.ach_transaction import AchSettlementStatus, AchTransaction
from openclear.domain.wsud_statement import WsudStatement
from openclear.domain.wsud_statement_transaction import WsudStatementTransaction

# The standard consumer-authorized ACH SEC codes plus the check-conversion codes (also
# consumer debits, just originated from a converted paper check) — see AchTransaction.sec_code.
# Corporate codes (CCD, CTX, ...) are deliberately excluded: a WSUD is a consumer remedy.
CONSUMER_SEC_CODES: frozenset[str] = frozenset({"PPD", "WEB", "TEL", "ARC", "BOC", "POP", "RCC"})

# Bumped whenever the *structure* of the disclosure changes in a way worth labeling
# distinctly (not on every wording tweak to the settings-backed text above — the
# cryptographic signature is over the actual statement_text_snapshot either way, so an
# out-of-sync version label is a labeling nicety, not an integrity issue).
CONSENT_DISCLOSURE_VERSION = "2026-07-30"


def get_consent_disclosure_text() -> str:
    return get_settings().wsud_consent_disclosure_text


def get_attestation_text() -> str:
    return get_settings().wsud_attestation_text


_FIELD_SEPARATOR = "\x1f"


@dataclass(frozen=True, slots=True)
class EligibleAchTransaction:
    transaction: AchTransaction
    already_covered: bool


def _canonical_fields(
    *,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    signed_by_user_id: uuid.UUID,
    signer_typed_name: str,
    ach_transaction_ids: list[uuid.UUID],
    consent_disclosure_version: str,
    statement_text: str,
    signed_at: datetime,
) -> bytes:
    """Same "stable, unambiguous serialization" technique as
    audit_log_service.py::_canonical_fields — used both when a statement is first
    signed and whenever it's re-verified later, so the two must always agree
    bit-for-bit on a legitimate, unmodified statement."""
    normalized_signed_at = signed_at.astimezone(timezone.utc).replace(tzinfo=None) if signed_at.tzinfo else signed_at
    parts = [
        str(tenant_id),
        str(customer_id),
        str(signed_by_user_id),
        signer_typed_name,
        ",".join(sorted(str(i) for i in ach_transaction_ids)),
        consent_disclosure_version,
        statement_text,
        normalized_signed_at.isoformat(),
    ]
    return _FIELD_SEPARATOR.join(parts).encode("utf-8")


def _sign(fields_bytes: bytes) -> str:
    private_key = load_private_key(get_settings().wsud_signing_private_key_path)
    return private_key.sign(fields_bytes, ec.ECDSA(hashes.SHA256())).hex()


def _verify(fields_bytes: bytes, signature: str) -> bool:
    public_key = load_public_key(get_settings().wsud_signing_public_key_path)
    try:
        public_key.verify(bytes.fromhex(signature), fields_bytes, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def list_wsud_eligible_transactions(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID
) -> list[EligibleAchTransaction]:
    """Every returned, consumer-SEC-code ACH transaction for this customer — shows, not
    hides, transactions already covered by a prior statement (`already_covered`), since a
    customer may legitimately need to re-attest (e.g. a reopened dispute)."""
    transactions = list(
        session.execute(
            select(AchTransaction)
            .where(
                AchTransaction.tenant_id == tenant_id,
                AchTransaction.customer_id == customer_id,
                AchTransaction.settlement_status == AchSettlementStatus.RETURNED,
                AchTransaction.sec_code.in_(CONSUMER_SEC_CODES),
            )
            .order_by(AchTransaction.effective_date.desc())
        )
        .scalars()
        .all()
    )
    if not transactions:
        return []
    covered_ids = set(
        session.execute(
            select(WsudStatementTransaction.ach_transaction_id).where(
                WsudStatementTransaction.tenant_id == tenant_id,
                WsudStatementTransaction.ach_transaction_id.in_([t.id for t in transactions]),
            )
        )
        .scalars()
        .all()
    )
    return [EligibleAchTransaction(transaction=t, already_covered=t.id in covered_ids) for t in transactions]


def sign_wsud_statement(
    session: Session,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    signed_by_user_id: uuid.UUID,
    signer_typed_name: str,
    ach_transaction_ids: list[uuid.UUID],
    signer_ip_address: str | None,
    signer_user_agent: str | None,
) -> WsudStatement:
    """Validates every given transaction actually belongs to this tenant+customer and is
    eligible (returned + consumer SEC code) before signing anything — a customer can
    never be tricked into attesting to a transaction that isn't theirs or that doesn't
    qualify. Raises ValueError on empty input or an ineligible transaction id."""
    signer_typed_name = signer_typed_name.strip()
    if not signer_typed_name:
        raise ValueError("Your typed name is required to sign.")
    if not ach_transaction_ids:
        raise ValueError("Select at least one transaction to include in this statement.")

    eligible_ids = {e.transaction.id for e in list_wsud_eligible_transactions(session, tenant_id, customer_id)}
    for txn_id in ach_transaction_ids:
        if txn_id not in eligible_ids:
            raise ValueError("One or more selected transactions are no longer eligible for a WSUD statement.")

    attestation_text = get_attestation_text()
    signed_at = datetime.now(timezone.utc)
    fields_bytes = _canonical_fields(
        tenant_id=tenant_id,
        customer_id=customer_id,
        signed_by_user_id=signed_by_user_id,
        signer_typed_name=signer_typed_name,
        ach_transaction_ids=ach_transaction_ids,
        consent_disclosure_version=CONSENT_DISCLOSURE_VERSION,
        statement_text=attestation_text,
        signed_at=signed_at,
    )

    statement = WsudStatement(
        tenant_id=tenant_id,
        customer_id=customer_id,
        signed_by_user_id=signed_by_user_id,
        signer_typed_name=signer_typed_name,
        signer_ip_address=signer_ip_address,
        signer_user_agent=signer_user_agent,
        consent_disclosure_version=CONSENT_DISCLOSURE_VERSION,
        statement_text_snapshot=attestation_text,
        signed_at=signed_at,
        signature_hex=_sign(fields_bytes),
    )
    session.add(statement)
    session.flush()

    for txn_id in ach_transaction_ids:
        session.add(
            WsudStatementTransaction(tenant_id=tenant_id, wsud_statement_id=statement.id, ach_transaction_id=txn_id)
        )
    session.flush()
    return statement


def verify_wsud_signature(session: Session, statement: WsudStatement) -> bool:
    """Recomputes and verifies against the stored signature — mirrors
    audit_log_service.py::verify_chain's per-entry check, just for this one document (no
    chain: a WSUD statement doesn't depend on what came before it)."""
    ach_transaction_ids = get_transaction_ids_for_statement(session, statement)
    fields_bytes = _canonical_fields(
        tenant_id=statement.tenant_id,
        customer_id=statement.customer_id,
        signed_by_user_id=statement.signed_by_user_id,
        signer_typed_name=statement.signer_typed_name,
        ach_transaction_ids=ach_transaction_ids,
        consent_disclosure_version=statement.consent_disclosure_version,
        statement_text=statement.statement_text_snapshot,
        signed_at=statement.signed_at,
    )
    return _verify(fields_bytes, statement.signature_hex)


def get_transaction_ids_for_statement(session: Session, statement: WsudStatement) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(WsudStatementTransaction.ach_transaction_id).where(
                WsudStatementTransaction.wsud_statement_id == statement.id
            )
        )
        .scalars()
        .all()
    )


def get_transactions_for_statement(session: Session, statement: WsudStatement) -> list[AchTransaction]:
    ids = get_transaction_ids_for_statement(session, statement)
    if not ids:
        return []
    return list(session.execute(select(AchTransaction).where(AchTransaction.id.in_(ids))).scalars().all())


def list_wsud_statements(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None = None
) -> list[WsudStatement]:
    """customer_id=None lists across every customer — the bank-wide oversight view;
    a real customer_id scopes to that customer's own statements, self-service."""
    stmt = select(WsudStatement).where(WsudStatement.tenant_id == tenant_id).order_by(WsudStatement.signed_at.desc())
    if customer_id is not None:
        stmt = stmt.where(WsudStatement.customer_id == customer_id)
    return list(session.execute(stmt).scalars().all())


def get_wsud_statement(
    session: Session, tenant_id: uuid.UUID, statement_id: uuid.UUID, *, customer_id: uuid.UUID | None = None
) -> WsudStatement | None:
    stmt = select(WsudStatement).where(WsudStatement.tenant_id == tenant_id, WsudStatement.id == statement_id)
    if customer_id is not None:
        stmt = stmt.where(WsudStatement.customer_id == customer_id)
    return session.execute(stmt).scalar_one_or_none()
