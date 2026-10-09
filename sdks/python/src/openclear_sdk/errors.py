# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""Errors the SDK raises. Every one is an OpenClearError, so `except OpenClearError` catches
them all; the subclasses let a caller handle the cases that call for different action."""

from __future__ import annotations

from typing import Any


class OpenClearError(Exception):
    """Something went wrong talking to OpenClear. `status_code` is the HTTP status (None if
    no response came back), `detail` the server's explanation, `body` its parsed response."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None, body: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.body = body


class APIConnectionError(OpenClearError):
    """No response: the server couldn't be reached, or didn't answer in time (after retries)."""


class AuthenticationError(OpenClearError):
    """401: the credentials or token were refused (a revoked client, a wrong secret)."""


class PermissionDeniedError(OpenClearError):
    """403: authenticated, but the client's security group doesn't allow this."""


class NotFoundError(OpenClearError):
    """404: no such item, or it belongs to another bank or customer (the same answer either way)."""


class ConflictError(OpenClearError):
    """409: the request conflicts with the current state (a duplicate, or a retry while the
    first attempt is still running)."""


class ValidationError(OpenClearError):
    """422: the request was understood but refused. `errors` lists field problems when the
    server gave them."""

    @property
    def errors(self) -> list[dict]:
        return self.detail if isinstance(self.detail, list) else []


class RateLimitError(OpenClearError):
    """429: too many requests (after retries). `retry_after` is the server's suggested wait, in seconds."""

    retry_after: float | None = None


class ServerError(OpenClearError):
    """5xx: OpenClear failed (after retries)."""


class WebhookVerificationError(OpenClearError):
    """A webhook's signature or timestamp didn't check out: don't trust its contents."""


class BulkUploadError(OpenClearError):
    """A bulk upload was rejected as a whole, or its import failed. `upload` is its status."""

    upload: dict | None = None


_OAUTH_AUTH_ERRORS = {"invalid_client", "invalid_grant", "unauthorized_client"}
_BY_STATUS = {401: AuthenticationError, 403: PermissionDeniedError, 404: NotFoundError, 409: ConflictError, 422: ValidationError, 429: RateLimitError}


def error_for(status_code: int, body: Any) -> OpenClearError:
    detail = None
    if isinstance(body, dict):
        detail = body.get("detail", body.get("error_description", body.get("error")))
    if isinstance(detail, list):
        message = "; ".join(
            f"{'.'.join(str(part) for part in d.get('loc', [])[1:]) or 'request'}: {d.get('msg', d)}" if isinstance(d, dict) else str(d)
            for d in detail
        )
    else:
        message = str(detail) if detail else (body if isinstance(body, str) and body else f"HTTP {status_code}")
    cls = _BY_STATUS.get(status_code, ServerError if status_code >= 500 else OpenClearError)
    if isinstance(body, dict) and body.get("error") in _OAUTH_AUTH_ERRORS:
        cls = AuthenticationError  # the token endpoint refused the credentials or the person's ID token
    return cls(f"{status_code}: {message}", status_code=status_code, detail=detail, body=body)
