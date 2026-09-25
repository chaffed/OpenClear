# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from typing import Protocol


class SmsProvider(Protocol):
    """Calling code (workers/tasks.py::notification_dispatch_job) depends only on this
    protocol via sms/factory.py::get_sms_provider() — never on a concrete provider.
    Mirrors ocr/base.py::OCRProvider's and email/base.py::EmailProvider's own shape."""

    name: str

    def send(self, *, to: str, body: str) -> None:
        """Raises on failure — see EmailProvider.send's own docstring for why this
        isn't a bool return."""
        ...
