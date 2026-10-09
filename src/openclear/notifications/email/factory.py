# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.config import Settings, get_settings
from openclear.notifications.email.base import EmailProvider

_PROVIDER_CACHE: dict[str, EmailProvider] = {}


def get_email_provider(settings: Settings | None = None) -> EmailProvider:
    settings = settings or get_settings()
    provider_name = settings.email_provider

    if provider_name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider_name]

    from openclear import plugins

    provider: EmailProvider = plugins.lookup("email_providers", provider_name, what="email provider")()

    _PROVIDER_CACHE[provider_name] = provider
    return provider
