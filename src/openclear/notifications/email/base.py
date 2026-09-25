# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from typing import Protocol


class EmailProvider(Protocol):
    """Calling code (workers/tasks.py::notification_dispatch_job) depends only on this
    protocol via email/factory.py::get_email_provider() — never on a concrete provider —
    so swapping SMTP for a vendor API is a config change, not a code change. Mirrors
    ocr/base.py::OCRProvider's own shape."""

    name: str

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Raises on failure (a connection error, a provider-rejected address, etc.) —
        never returns a bool, so a caller can't accidentally ignore a failed send the
        way a bool return invites. The caller (the dispatch job) is what decides how to
        record that failure."""
        ...
