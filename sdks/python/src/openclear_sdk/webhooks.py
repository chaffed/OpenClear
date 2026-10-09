# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""Checking that a webhook really came from OpenClear (Standard Webhooks,
https://www.standardwebhooks.com/). Verify before acting on one, using the endpoint's
secret (`whsec_...`) and the raw request body, before any JSON parsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from openclear_sdk.errors import WebhookVerificationError

DEFAULT_TOLERANCE_SECONDS = 300


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The `webhook-signature` value OpenClear sends (for tests of your own receiver)."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{message_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def verify(
    secret: str, headers: Mapping[str, str], body: bytes | str, *, tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Returns the event (`type`, `timestamp`, `data`) if the signature matches and the
    timestamp is recent; raises WebhookVerificationError otherwise. Use `webhook-id` (in
    the headers) to skip an event you've already handled: retries keep the same id."""
    lower = {k.lower(): v for k, v in headers.items()}
    message_id, timestamp, signatures = lower.get("webhook-id"), lower.get("webhook-timestamp"), lower.get("webhook-signature")
    if not (message_id and timestamp and signatures):
        raise WebhookVerificationError("Missing webhook-id, webhook-timestamp or webhook-signature header.")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("webhook-timestamp isn't a number.") from None
    if abs((time.time() if now is None else now) - sent_at) > tolerance_seconds:
        raise WebhookVerificationError("The webhook's timestamp is too old or in the future (a replay, or a clock out of sync).")
    raw = body.encode() if isinstance(body, str) else body
    try:
        expected = sign(secret, message_id, sent_at, raw)
    except (ValueError, TypeError):
        raise WebhookVerificationError("The secret isn't a valid whsec_ secret.") from None
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures.split()):
        raise WebhookVerificationError("The webhook's signature doesn't match.")
    try:
        return json.loads(raw)
    except ValueError:
        raise WebhookVerificationError("The webhook's body isn't JSON.") from None
