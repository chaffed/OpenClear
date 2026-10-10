# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Logging (SIEM_PLAN.md S1). With OPENCLEAR_LOG_FORMAT=json, every line on stdout is one JSON
object with the time (UTC, ISO 8601), level, logger and message, and, during a request, its
id, the client IP, the bank and who's signed in (web/request_context.py). A container log
collector (Splunk's OpenTelemetry Collector, Fluent Bit, Datadog, CloudWatch) can then
search on them. Text (the default) is unchanged.

Never logged: request bodies, query strings, tokens, cookies, passwords."""

import json
import logging
import sys
from datetime import datetime, timezone

_HANDLER_NAME = "openclear-json"


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
