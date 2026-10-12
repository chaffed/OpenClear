# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""One record (services/security_events.as_record), several wire forms: JSON, CEF and LEEF
for syslog, each inside an RFC 5424 syslog message; JSON for Splunk HEC and HTTPS."""

import json
from datetime import datetime, timezone
from functools import lru_cache

# RFC 5424 facility 13, "log audit".
SYSLOG_FACILITY = 13
_SYSLOG_SEVERITY = {"low": 6, "medium": 5, "high": 4, "critical": 2}  # informational, notice, warning, critical
_CEF_SEVERITY = {"low": 3, "medium": 5, "high": 8, "critical": 10}
_LEEF_SEVERITY = {"low": 2, "medium": 5, "high": 8, "critical": 10}
FORMATS = ("json", "cef", "leef")


@lru_cache(maxsize=1)
def product_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openclear")
    except PackageNotFoundError:
        return "dev"


def to_json(record: dict) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=False, default=str)


def _flat(record: dict) -> dict[str, str]:
    """The record's fields under CEF's standard keys, with OpenClear's own in custom ones.
    Shared by CEF and LEEF so the two carry the same facts."""
    event, actor, target = record.get("event", {}), record.get("actor") or {}, record.get("target") or {}
    source, request, bank = record.get("source") or {}, record.get("request") or {}, record.get("bank") or {}
    fields = {
        "rt": record.get("time"),
        "externalId": record.get("id"),
        "cat": event.get("category"),
        "outcome": event.get("outcome"),
        "reason": event.get("reason"),
        "src": source.get("ip"),
        "requestClientApplication": source.get("user_agent"),
        "suser": actor.get("email") or actor.get("id"),
        "suid": actor.get("id"),
        "cs1Label": "bank" if bank else None, "cs1": bank.get("slug") or bank.get("id"),
        "cs2Label": "actorType" if actor else None, "cs2": actor.get("type"),
        "cs3Label": "targetType" if target else None, "cs3": target.get("type"),
        "duid": target.get("id"),
        "duser": target.get("name"),
        "cs4Label": "requestId" if request.get("id") else None, "cs4": request.get("id"),
        "cs5Label": "channel" if request.get("channel") else None, "cs5": request.get("channel"),
        "cs6Label": "details" if record.get("details") else None,
        "cs6": json.dumps(record["details"], separators=(",", ":"), default=str) if record.get("details") else None,
    }
    if (record.get("audit") or {}).get("sequence"):
        fields["cn1Label"], fields["cn1"] = "auditSequence", str(record["audit"]["sequence"])
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}


def _cef_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _cef_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("=", "\\=").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")


def to_cef(record: dict) -> str:
    event = record.get("event", {})
    name = event.get("name", "")
    summary = f"{name} ({event.get('outcome', '')})"
    header = "|".join(_cef_header(part) for part in (
        "CEF:0", "OpenClear", "OpenClear", product_version(), name, summary, str(_CEF_SEVERITY.get(event.get("severity"), 5))))
    return header + "|" + " ".join(f"{key}={_cef_value(value)}" for key, value in _flat(record).items())


def _leef_value(value: str) -> str:
    # Tab separates attributes; a value never contains one, or a line break.
    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def to_leef(record: dict) -> str:
    event = record.get("event", {})
    header = "|".join(_cef_header(part) for part in ("LEEF:2.0", "OpenClear", "OpenClear", product_version(), event.get("name", "")))
    fields = _flat(record)
    attributes = {"devTime": fields.pop("rt", ""), "devTimeFormat": "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX",
                  "sev": str(_LEEF_SEVERITY.get(event.get("severity"), 5)),
                  "usrName": fields.pop("suser", None), "identSrc": fields.get("src"), **fields}
    return header + "|x09|" + "\t".join(f"{k}={_leef_value(v)}" for k, v in attributes.items() if v)


def render(record: dict, fmt: str) -> str:
    return {"json": to_json, "cef": to_cef, "leef": to_leef}[fmt](record)


def _sd_free(value: str, limit: int) -> str:
    """A syslog header field: printable US-ASCII, no spaces, or the nil value."""
    cleaned = "".join(ch for ch in value if 33 <= ord(ch) <= 126)[:limit]
    return cleaned or "-"


def syslog_message(record: dict, fmt: str, *, hostname: str, now: datetime | None = None) -> str:
    """An RFC 5424 message: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID - MSG. The
    message id is the event name; there's no structured data (the payload carries it)."""
    severity = _SYSLOG_SEVERITY.get(record.get("event", {}).get("severity"), 5)
    timestamp = record.get("time") or (now or datetime.now(timezone.utc)).isoformat()
    msgid = _sd_free(record.get("event", {}).get("name", ""), 32)
    return (f"<{SYSLOG_FACILITY * 8 + severity}>1 {_sd_free(timestamp, 64)} {_sd_free(hostname, 255)} openclear - "
            f"{msgid} - {render(record, fmt)}")


def octet_frame(message: str) -> bytes:
    """RFC 5425 / RFC 6587 octet counting: the length in bytes, a space, the message."""
    data = message.encode("utf-8")
    return str(len(data)).encode() + b" " + data
