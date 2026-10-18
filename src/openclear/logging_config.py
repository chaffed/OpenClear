# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Logging (SIEM_PLAN.md S1). With OPENCLEAR_LOG_FORMAT=json, every line on stdout is one JSON
object with the time (UTC, ISO 8601), level, logger and message, and, during a request, its
id, the client IP, the bank and who's signed in (web/request_context.py). A container log
collector (Splunk's OpenTelemetry Collector, Fluent Bit, Datadog, CloudWatch) can then
search on them. In text (the default), control characters in a message are escaped, so a value
from a request (an email typed at sign-in, a path) can't forge a log line (ASVS 5.0 16.4.1);
JSON escapes them anyway. Production should use JSON (config.assert_production_safe warns).

Never logged: request bodies, query strings, tokens, cookies, passwords."""

import json
import logging
import sys
from datetime import datetime, timezone

_HANDLER_NAME = "openclear-json"
_CONTROL = {c: f"\\x{c:02x}" for c in range(32) if c != 9} | {10: "\\n", 13: "\\r", 127: "\\x7f"}


def escape_control_characters(text: str) -> str:
    """Line breaks and other control characters (tabs aside) as visible escapes."""
    return text.translate(_CONTROL)


def _install_escaping_record_factory() -> None:
    """Every log record, wherever it's logged and whichever handler writes it, gets its message
    escaped once, at creation. Tracebacks (exc_info) are formatted separately and keep their
    lines."""
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_openclear_escaping", False):
        return

    def escaping_factory(*args, **kwargs):
        record = factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # a malformed format string: leave it for the handler to report
            return record
        escaped = escape_control_characters(message)
        if escaped != message:
            record.msg, record.args = escaped, None
        return record

    escaping_factory._openclear_escaping = True
    logging.setLogRecordFactory(escaping_factory)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from openclear.web import request_context

        line: dict = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = request_context.current()
        if context is not None:
            line["request"] = {k: v for k, v in (("id", context.request_id), ("edge_id", context.edge_id),
                                                 ("channel", context.channel)) if v}
            line["client"] = {"ip": context.client_ip}
            if context.tenant_slug:
                line["bank"] = context.tenant_slug
            if context.user_id:
                line["user"] = {"id": str(context.user_id), **({"api_client_id": str(context.api_client_id)} if context.api_client_id else {})}
        for key in ("http", "security_event"):
            value = getattr(record, key, None)
            if value is not None:
                line[key] = value
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure(settings=None) -> None:
    """Applies OPENCLEAR_LOG_FORMAT and OPENCLEAR_LOG_LEVEL. Safe to call more than once."""
    from openclear.config import get_settings

    settings = settings or get_settings()
    # httpx logs every outbound URL at INFO, query string and all: a webhook or provider URL
    # can carry a token. Only its warnings are kept.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("openclear.access")
    if settings.log_format != "json":
        _install_escaping_record_factory()
        # uvicorn already writes an access line per request in text mode.
        access.setLevel(logging.WARNING)
        return
    root = logging.getLogger()
    if not any(getattr(h, "name", None) == _HANDLER_NAME for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.name = _HANDLER_NAME
        handler.setFormatter(JsonFormatter())
        for existing in list(root.handlers):
            root.removeHandler(existing)
        root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    access.setLevel(logging.INFO)
    # uvicorn's own loggers go through the JSON handler; its access log is replaced by
    # openclear.access (with the request's context), so it's quietened.
    for name in ("uvicorn", "uvicorn.error"):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
