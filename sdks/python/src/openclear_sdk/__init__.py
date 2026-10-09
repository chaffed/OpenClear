# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""The OpenClear SDK for Python: a typed client for OpenClear's API (positive pay and ACH
exception decisions), for banking cores, online banking providers and a bank's own systems.

    from openclear_sdk import OpenClear, webhooks

See README.md."""

from openclear_sdk import webhooks
from openclear_sdk._generated import API_VERSION
from openclear_sdk.bulk import BulkResult
from openclear_sdk.client import OpenClear
from openclear_sdk.errors import (
    APIConnectionError,
    AuthenticationError,
    BulkUploadError,
    ConflictError,
    NotFoundError,
    OpenClearError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
    WebhookVerificationError,
)

__all__ = [
    "API_VERSION", "APIConnectionError", "AuthenticationError", "BulkResult", "BulkUploadError", "ConflictError",
    "NotFoundError", "OpenClear", "OpenClearError", "PermissionDeniedError", "RateLimitError", "ServerError",
    "ValidationError", "WebhookVerificationError", "webhooks",
]
