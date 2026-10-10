# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Model risk management (COMPLIANCE_PLAN.md C2; SR 11-7, OCC 2011-12): evidence that the
bank's matching rules and scoring do what it thinks they do.

**Replay** ("shadow match") runs a historical X9.37 cash letter or NACHA file through
today's rules and compares each item with what actually happened to it, giving a
confidence and variance report. **Anomaly capture** builds synthetic items with known
fraud vectors (a duplicate check, an altered amount, a check on the wrong account, an ACH
debit over its limit or from an unknown originator, malformed files) and checks that every
one is caught, and that a clean item isn't.

Both are a **dry run**: the real ingestion code runs (the bank's own settings, rules and
the plugins it turned on), inside a savepoint that's rolled back. Nothing they create
survives: no items, exceptions, notifications, webhooks or jobs. A replay leaves out the
check images, which are stored outside the database, so the OCR payee check doesn't run.
What's kept is a ControlRun with the report, and an audit entry."""

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.bulk_import.nacha import NachaParseError, parse_nacha_file
from openclear.bulk_import.x937 import X937ParseError, parse_x937_file
from openclear.domain.ach_transaction import AchMatchStatus, AchTransaction, AchTransactionSource, AchTransactionType
from openclear.domain.control_run import ControlRun
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItem, IssuedItemSource, IssuedItemStatus
from openclear.domain.paid_item import PaidItem, PaidItemMatchStatus, PaidItemSettlementStatus, PaidItemSource

REPLAY = "mrm_replay"
ANOMALY_CAPTURE = "anomaly_capture"
MAX_REPLAY_ITEMS = 20_000


class ReplayError(ValueError):
    """The file can't be replayed; the message is fit to show."""


@contextmanager
def dry_run(session: Session):
    """Runs the block inside a savepoint that's always rolled back."""
    savepoint = session.begin_nested()
    try:
        yield
    finally:
        if savepoint.is_active:
            savepoint.rollback()
        session.expire_all()


def _item_savepoint(session: Session, fn):
    """One item's ingestion, in its own savepoint so a failure doesn't spoil the rest."""
    with session.begin_nested():
        return fn()


# --- Replay ---------------------------------------------------------------------------------

@dataclass
class _Outcome:
    label: str
    replayed: str  # "matched", "flagged" or "unhandled"
    exception_types: list[str]
    history: str  # "paid", "returned", "flagged_open" or "none"
    history_flagged: bool | None
    error: str | None = None


def _detect(content: bytes) -> str:
    stripped = content.lstrip()
    if stripped[:1] == b"1" and stripped[:3] == b"101":
        return "nacha"
    return "x937"


def _history(status_matched: bool, settlement: str) -> str:
    if settlement == "returned":
        return "returned"
    if settlement == "paid":
        return "paid"
    return "flagged_open" if not status_matched else "paid"


def _replay_check(session: Session, tenant_id: uuid.UUID, content: bytes) -> list[_Outcome]:
    from openclear.networks.check.bulk_import import x937_item_label
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
    from openclear.services import account_service

    try:
        items = parse_x937_file(content)
    except X937ParseError as exc:
        raise ReplayError(f"That X9.37 file couldn't be read: {exc}") from None
    if len(items) > MAX_REPLAY_ITEMS:
        raise ReplayError(f"At most {MAX_REPLAY_ITEMS} items per replay.")
    outcomes: list[_Outcome] = []
    for item in items:
        label = x937_item_label(item)
        account = None
        for number in (item.account_number, item.on_us.partition("/")[0].replace(" ", "")):
            account = account_service.get_account_by_number(session, tenant_id, number) if number else None
            if account:
                break
        if account is None or not item.check_number:
            outcomes.append(_Outcome(label, "unhandled", [], "none", None, "No account or check number on file for this item"))
            continue
        # What happened to this item: the earliest paid item with the same account, number and amount.
        original = session.execute(select(PaidItem).where(
            PaidItem.tenant_id == tenant_id, PaidItem.account_id == account.id, PaidItem.check_number == item.check_number,
            PaidItem.presented_amount == item.amount,
        ).order_by(PaidItem.created_at)).scalars().first()
        history, history_flagged = "none", None
        if original is not None:
            history_flagged = original.match_status == PaidItemMatchStatus.EXCEPTION
            history = _history(not history_flagged, original.settlement_status.value)
            # Rewind: the original mustn't count as an earlier payment of itself, and its
            # issued check is outstanding again (all inside the dry run).
            original.settlement_status = PaidItemSettlementStatus.PENDING
            if original.matched_issued_item_id:
                issued = session.get(IssuedItem, original.matched_issued_item_id)
                if issued is not None and issued.status == IssuedItemStatus.PAID:
                    issued.status = IssuedItemStatus.OUTSTANDING
            session.flush()
        try:
            paid = _item_savepoint(session, lambda: ingest_paid_item(session, tenant_id, PaidItemSubmission(
                account_id=account.id, check_number=item.check_number, presented_amount=item.amount,
                presented_date=item.presented_date, source=PaidItemSource.BULK_FILE)))
        except Exception as exc:  # noqa: BLE001 -- an item the rules couldn't handle is part of the report
            outcomes.append(_Outcome(label, "unhandled", [], history, history_flagged, str(exc)[:300]))
            continue
        types = _types(session, paid.id) if paid.match_status == PaidItemMatchStatus.EXCEPTION else []
        outcomes.append(_Outcome(label, "flagged" if types else "matched", types, history, history_flagged))
    return outcomes


def _replay_ach(session: Session, tenant_id: uuid.UUID, content: bytes) -> list[_Outcome]:
    from openclear.networks.ach.bulk_import import _submission_from_nacha_entry, nacha_entry_label
    from openclear.networks.ach.ingestion import ingest_ach_transaction

    try:
        entries = parse_nacha_file(content)
    except NachaParseError as exc:
        raise ReplayError(f"That NACHA file couldn't be read: {exc}") from None
    if len(entries) > MAX_REPLAY_ITEMS:
        raise ReplayError(f"At most {MAX_REPLAY_ITEMS} entries per replay.")
    outcomes: list[_Outcome] = []
    for entry in entries:
        label = nacha_entry_label(entry)
        original = session.execute(select(AchTransaction).where(
            AchTransaction.tenant_id == tenant_id, AchTransaction.trace_number == entry.trace_number)).scalar_one_or_none()
        history, history_flagged = "none", None
        if original is not None:
            history_flagged = original.match_status == AchMatchStatus.EXCEPTION
            history = _history(not history_flagged, original.settlement_status.value)
            # Trace numbers are unique per bank: move the original's aside (inside the dry run).
            original.trace_number = f"replay-{original.id.hex[:24]}"
            session.flush()
        try:
            def ingest():
                submission = _submission_from_nacha_entry(session, tenant_id, entry)
                return ingest_ach_transaction(session, tenant_id, submission)
            txn = _item_savepoint(session, ingest)
        except Exception as exc:  # noqa: BLE001 -- an item the rules couldn't handle is part of the report
            outcomes.append(_Outcome(label, "unhandled", [], history, history_flagged, str(exc)[:300]))
            continue
        types = _types(session, txn.id) if txn.match_status == AchMatchStatus.EXCEPTION else []
        outcomes.append(_Outcome(label, "flagged" if types else "matched", types, history, history_flagged))
    return outcomes


def _types(session: Session, source_item_id: uuid.UUID) -> list[str]:
    found = session.execute(select(ExceptionItem.exception_types).where(ExceptionItem.source_item_id == source_item_id)).scalar()
    return [t for t in (found or "").split(",") if t]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _variance_report(network: str, outcomes: list[_Outcome]) -> dict:
    """Compared items are those found in history. Of those:
    - agreement: today's rules flag it exactly when they flagged it then;
    - false positive: flagged today, but the bank paid it;
    - missed return: not flagged today, but the bank returned it."""
    compared = [o for o in outcomes if o.history != "none" and o.replayed != "unhandled"]
    agree = [o for o in compared if (o.replayed == "flagged") == bool(o.history_flagged)]
    paid = [o for o in compared if o.history == "paid"]
    returned = [o for o in compared if o.history == "returned"]
    false_positives = [o for o in paid if o.replayed == "flagged"]
    missed = [o for o in returned if o.replayed == "matched"]
    newly_flagged = [o for o in compared if o.replayed == "flagged" and not o.history_flagged]
    no_longer = [o for o in compared if o.replayed == "matched" and o.history_flagged]
    unhandled = [o for o in outcomes if o.replayed == "unhandled"]
    by_type: dict[str, int] = {}
    for o in outcomes:
        for t in o.exception_types:
            by_type[t] = by_type.get(t, 0) + 1

    def items(rows):
        return [{"item": o.label, "replayed": o.replayed, "exception_types": o.exception_types, "history": o.history,
                 **({"error": o.error} if o.error else {})} for o in rows[:200]]

    return {
        "network": network,
        "items": len(outcomes),
        "compared_with_history": len(compared),
        "not_in_history": sum(1 for o in outcomes if o.history == "none" and o.replayed != "unhandled"),
        "flagged": sum(1 for o in outcomes if o.replayed == "flagged"),
        "matched": sum(1 for o in outcomes if o.replayed == "matched"),
        "match_rate": _rate(len(agree), len(compared)),
        "false_positive_rate": _rate(len(false_positives), len(paid)),
        "missed_return_rate": _rate(len(missed), len(returned)),
        "unhandled_rate": _rate(len(unhandled), len(outcomes)),
        "exception_types": dict(sorted(by_type.items())),
        "variances": {
            "newly_flagged": items(newly_flagged),
            "no_longer_flagged": items(no_longer),
            "false_positives": items(false_positives),
            "missed_returns": items(missed),
            "unhandled": items(unhandled),
        },
        "notes": ["Check images aren't replayed, so the OCR payee check doesn't run."] if network == "check" else [],
    }


def replay(session: Session, tenant_id: uuid.UUID, *, filename: str, content: bytes, user_id: uuid.UUID | None,
           channel: str = "api", run: ControlRun | None = None) -> ControlRun:
    """Replays the file in a dry run and keeps the report: in a new run, or in `run` (one
    queued by services/long_runs.py). The caller commits."""
    from openclear.services import audit_log_service

    if not content:
        raise ReplayError("The file is empty.")
    network = "ach" if _detect(content) == "nacha" else "check"
    with dry_run(session):
        outcomes = (_replay_ach if network == "ach" else _replay_check)(session, tenant_id, content)
    report = {"file": filename[:200], **_variance_report(network, outcomes)}
    match_rate = report["match_rate"]
    summary = (f"Replayed {report['items']} {'ACH entries' if network == 'ach' else 'checks'} from {filename[:120]}: "
               + (f"{match_rate:.1%} agree with history" if match_rate is not None else "none found in history"))
    if run is None:
        run = ControlRun(tenant_id=tenant_id, kind=REPLAY, passed=None, requested_by_user_id=user_id)
        session.add(run)
    run.summary, run.report = summary[:500], {**report, "state": "done"}
    session.flush()
    audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel, action="mrm.replay", summary=summary[:500],
                                    resource_type="control_run", resource_id=run.id,
                                    metadata={k: report[k] for k in ("network", "items", "match_rate", "false_positive_rate",
                                                                      "missed_return_rate", "unhandled_rate")})
    return run


# --- Anomaly capture ------------------------------------------------------------------------

@dataclass
class _Probe:
    key: str
    title: str
    expect: str  # an exception type it must raise, "clean" (must match), or "rejected" (the parser must refuse it)
    got: str = ""
    caught: bool = False


def _synthetic_check_probes(session: Session, tenant_id: uuid.UUID, today: date) -> list[_Probe]:
    from openclear.domain.account import Account
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    tag = uuid.uuid4().hex[:8]
    account = Account(tenant_id=tenant_id, account_number=f"SYN{tag}", name="Synthetic control test account")
    other = Account(tenant_id=tenant_id, account_number=f"SYO{tag}", name="Synthetic control test account (other)")
    session.add_all([account, other])
    session.flush()
    for number, amount in (("900001", "250.00"), ("900002", "1200.00"), ("900003", "75.10")):
        session.add(IssuedItem(tenant_id=tenant_id, account_id=account.id, check_number=number, amount=Decimal(amount),
                               payee_name="Synthetic Payee", issue_date=today - timedelta(days=5), source=IssuedItemSource.API))
    session.flush()

    def present(account_id, number, amount):
        return _item_savepoint(session, lambda: ingest_paid_item(session, tenant_id, PaidItemSubmission(
            account_id=account_id, check_number=number, presented_amount=Decimal(amount), presented_date=today)))

    probes = [
        _Probe("check_clean", "A check that matches what was issued", "clean"),
        _Probe("check_duplicate_serial", "The same check presented twice", "duplicate_paid"),
        _Probe("check_altered_amount", "A check whose amount was altered", "amount_mismatch"),
        _Probe("check_wrong_account", "A check drawn on the wrong account", "not_in_file"),
    ]
    runs = [
        lambda: present(account.id, "900001", "250.00"),
        lambda: present(account.id, "900001", "250.00"),  # after the first one paid
        lambda: present(account.id, "900002", "12000.00"),
        lambda: present(other.id, "900003", "75.10"),
    ]
    for probe, run in zip(probes, runs):
        _judge(session, probe, run)
    return probes


def _synthetic_ach_probes(session: Session, tenant_id: uuid.UUID, today: date) -> list[_Probe]:
    from openclear.domain.account import Account
    from openclear.domain.ach_authorization_rule import AchAuthorizationRule
    from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction

    tag = uuid.uuid4().hex[:8]
    account = Account(tenant_id=tenant_id, account_number=f"SYA{tag}", name="Synthetic control test account (ACH)")
    session.add(account)
    session.flush()
    session.add(AchAuthorizationRule(tenant_id=tenant_id, account_id=account.id, originator_id="9SYNTHETIC", originator_name="Synthetic Originator", max_amount=Decimal("500.00"),
                                     effective_date=today - timedelta(days=30)))
    session.flush()

    def debit(originator, amount, n):
        return _item_savepoint(session, lambda: ingest_ach_transaction(session, tenant_id, AchTransactionSubmission(
            account_id=account.id, originator_id=originator, originator_name="Synthetic Originator", receiver_id=None,
            amount=Decimal(amount), transaction_type=AchTransactionType.DEBIT, sec_code="PPD", trace_number=f"SYN{tag}{n:07d}",
            effective_date=today, source=AchTransactionSource.API)))

    probes = [
        _Probe("ach_clean", "An authorized ACH debit within its limit", "clean"),
        _Probe("ach_altered_amount", "An ACH debit whose amount was altered past its limit", "amount_exceeds_limit"),
        _Probe("ach_unknown_originator", "An ACH debit from an originator the account never authorized", "unauthorized_originator"),
    ]
    runs = [lambda: debit("9SYNTHETIC", "120.00", 1), lambda: debit("9SYNTHETIC", "5120.00", 2), lambda: debit("9UNKNOWN01", "120.00", 3)]
    for probe, run in zip(probes, runs):
        _judge(session, probe, run)
    return probes


def _judge(session: Session, probe: _Probe, run) -> None:
    try:
        item = run()
    except Exception as exc:  # noqa: BLE001 -- an error is a failed probe, not a failed test run
        probe.got, probe.caught = f"error: {str(exc)[:200]}", False
        return
    types = _types(session, item.id) if item.match_status.value == "exception" else []
    probe.got = ",".join(types) or "matched"
    probe.caught = (not types) if probe.expect == "clean" else probe.expect in types


def _malformed_file_probes() -> list[_Probe]:
    """Files that must be refused cleanly, not half-read (the spec's malformed fields). As in
    bulk import, a file with nothing in it to import counts as refused."""
    entry = {"transaction_code": "27", "account": "1234567890", "amount_cents": 12000, "individual_id": "SYN0000001",
             "trace": "123456780000001"}
    good = build_nacha([entry])
    cases = [
        ("x937_truncated", "An X9.37 file cut off mid-record", "x937", b"\x00\x00\x00\x50" + b"01" + b"0" * 20),
        ("x937_bad_amount", "An X9.37 check whose amount isn't a number", "x937", _x937_with_bad_amount()),
        ("nacha_no_header", "A NACHA file with entries but no batch header", "nacha",
         "\n".join(line for line in good.decode().splitlines() if not line.startswith("5")).encode()),
        ("nacha_bad_amount", "A NACHA entry whose amount isn't a number", "nacha", build_nacha([{**entry, "amount_text": "00000X2000"}])),
        ("nacha_bad_totals", "A NACHA file whose control totals don't add up", "nacha", build_nacha([entry], declared_debit_cents=99)),
        ("nacha_binary", "Binary content sent as a NACHA file", "nacha", bytes(range(256)) * 4),
    ]
    probes = []
    for key, title, kind, content in cases:
        probe = _Probe(key, title, "rejected")
        try:
            parsed = parse_x937_file(content) if kind == "x937" else parse_nacha_file(content)
            probe.got, probe.caught = (f"accepted {len(parsed)} items", False) if parsed else ("rejected: nothing to import", True)
        except (X937ParseError, NachaParseError) as exc:
            probe.got, probe.caught = f"rejected: {str(exc)[:200]}", True
        except Exception as exc:  # noqa: BLE001 -- a crash isn't a clean rejection
            probe.got, probe.caught = f"crashed: {type(exc).__name__}", False
        probes.append(probe)
    return probes


def _x937_with_bad_amount() -> bytes:
    def record(kind: str, fields: list[tuple[int, int, str]] = ()) -> bytes:
        chars = list(kind + " " * 78)
        for start, length, value in fields:
            chars[start:start + length] = list(value.ljust(length)[:length])
        text = "".join(chars).encode()
        return len(text).to_bytes(4, "big") + text

    return b"".join([
        record("01", [(2, 2, "03"), (4, 1, "T")]),
        record("10", [(2, 2, "01"), (4, 9, "123456780"), (13, 9, "123456780"), (22, 8, "20260115")]),
        record("20", [(2, 2, "01")]),
        record("25", [(2, 15, "900001".rjust(15)), (18, 8, "12345678"), (26, 1, "0"), (27, 20, "1001/".rjust(20)), (47, 10, "12AB5X0000")]),
        record("70", [(2, 4, "0001"), (6, 12, "000000000000")]),
        record("90", [(8, 8, "00000001"), (16, 14, "00000000000000")]),
        record("99", [(16, 8, "00000001"), (24, 16, "0000000000000000")]),
    ])


def build_nacha(entries: list[dict], *, company_id: str = "9SYNTHETIC", company_name: str = "SYNTHETIC CO", sec_code: str = "PPD",
                effective: date = date(2026, 1, 15), declared_debit_cents: int | None = None) -> bytes:
    """A one-batch NACHA file. Each entry: transaction_code, account, amount_cents,
    individual_id, trace, and optionally amount_text (a raw amount field, to test a bad one)."""
    def record(fields: list[tuple[int, int, str]]) -> str:
        chars = [" "] * 94
        for start, length, value in fields:
            chars[start:start + length] = list(str(value).ljust(length)[:length])
        return "".join(chars)

    rdfi, ymd = "12345678", effective.strftime("%y%m%d")
    debit = sum(e["amount_cents"] for e in entries if e["transaction_code"][1] in "56789")
    credit = sum(e["amount_cents"] for e in entries if e["transaction_code"][1] not in "56789")
    entry_hash = str(int(rdfi) * len(entries) % 10**10).zfill(10)
    debit_declared = debit if declared_debit_cents is None else declared_debit_cents
    lines = [record([(0, 1, "1"), (1, 2, "01"), (3, 10, " 123456780"), (13, 10, " 123456780"), (23, 6, ymd), (29, 4, "0900"),
                     (33, 1, "A"), (34, 3, "094"), (37, 2, "10"), (39, 1, "1"), (40, 23, "DEST BANK"), (63, 23, "ORIGIN BANK")]),
             record([(0, 1, "5"), (1, 3, "200"), (4, 16, company_name), (40, 10, company_id), (50, 3, sec_code), (53, 10, "PAYMENT"),
                     (69, 6, ymd), (78, 1, "1"), (79, 8, "12345678"), (87, 7, "0000001")])]
    for e in entries:
        amount = e.get("amount_text") or str(e["amount_cents"]).zfill(10)
        lines.append(record([(0, 1, "6"), (1, 2, e["transaction_code"]), (3, 8, rdfi), (11, 1, "0"), (12, 17, e["account"]),
                             (29, 10, amount), (39, 15, e.get("individual_id") or ""), (54, 22, "SYNTHETIC RECEIVER"), (78, 1, "0"),
                             (79, 15, e["trace"])]))
    count = str(len(entries)).zfill(6)
    lines.append(record([(0, 1, "8"), (1, 3, "200"), (4, 6, count), (10, 10, entry_hash), (20, 12, str(debit_declared).zfill(12)),
                         (32, 12, str(credit).zfill(12)), (44, 10, company_id), (79, 8, "12345678"), (87, 7, "0000001")]))
    lines.append(record([(0, 1, "9"), (1, 6, "000001"), (7, 6, "000001"), (13, 8, count.zfill(8)), (21, 10, entry_hash),
                         (31, 12, str(debit_declared).zfill(12)), (43, 12, str(credit).zfill(12))]))
    return ("\n".join(lines) + "\n").encode()


def anomaly_capture(session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None, channel: str = "api",
                    today: date | None = None) -> ControlRun:
    """Runs every probe in a dry run and keeps the result. Passes only if every anomaly was
    caught and the clean items weren't flagged. The caller commits."""
    from openclear.services import audit_log_service

    today = today or date.today()
    with dry_run(session):
        probes = _synthetic_check_probes(session, tenant_id, today) + _synthetic_ach_probes(session, tenant_id, today)
    probes += _malformed_file_probes()
    passed = all(p.caught for p in probes)
    missed = [p for p in probes if not p.caught]
    summary = (f"All {len(probes)} synthetic anomalies and clean items handled as expected" if passed
               else f"{len(missed)} of {len(probes)} synthetic probes not handled as expected: " + ", ".join(p.key for p in missed))
    report = {"probes": [{"key": p.key, "title": p.title, "expected": p.expect, "got": p.got, "caught": p.caught} for p in probes],
              "caught": sum(1 for p in probes if p.caught), "total": len(probes)}
    run = ControlRun(tenant_id=tenant_id, kind=ANOMALY_CAPTURE, passed=passed, summary=summary[:500], report=report,
                     requested_by_user_id=user_id)
    session.add(run)
    session.flush()
    audit_log_service.record_action(session, tenant_id, actor_user_id=user_id, channel=channel, action="mrm.anomaly_capture",
                                    summary=summary[:500], resource_type="control_run", resource_id=run.id,
                                    metadata={"passed": passed, "caught": report["caught"], "total": report["total"],
                                              "missed": [p.key for p in missed]})
    return run


def recent_runs(session: Session, tenant_id: uuid.UUID, kind: str | None = None, limit: int = 20) -> list[ControlRun]:
    stmt = select(ControlRun).where(ControlRun.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(ControlRun.kind == kind)
    return list(session.execute(stmt.order_by(ControlRun.started_at.desc()).limit(limit)).scalars())


def latest(session: Session, tenant_id: uuid.UUID, kind: str) -> ControlRun | None:
    runs = recent_runs(session, tenant_id, kind, limit=1)
    return runs[0] if runs else None
