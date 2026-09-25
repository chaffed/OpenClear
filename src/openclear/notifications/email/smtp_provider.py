# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import smtplib
from email.message import EmailMessage

from openclear.config import get_settings


class SmtpEmailProvider:
    """The only real email provider for v1 -- plain SMTP works with any relay (a local
    dev mailbox like Mailpit, or the SMTP endpoint every major transactional-email
    vendor also exposes), so this needs no vendor SDK as a hard dependency. One
    connection per send() call -- workers/tasks.py::notification_dispatch_job batches
    sends itself by holding this provider instance for a whole dispatch run rather than
    reconnecting is left to a future optimization if send volume ever warrants it; SMTP's
    per-message connection overhead is small relative to the ~30s polling interval this
    runs on."""

    name = "smtp"

    def send(self, *, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        if not settings.smtp_host:
            raise RuntimeError("OPENCLEAR_SMTP_HOST is not configured -- cannot send email")

        message = EmailMessage()
        message["From"] = settings.smtp_from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as client:
            if settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password or "")
            client.send_message(message)
