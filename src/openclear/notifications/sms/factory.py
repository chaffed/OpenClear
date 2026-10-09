# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.config import Settings, get_settings
from openclear.notifications.sms.base import SmsProvider

_PROVIDER_CACHE: dict[str, SmsProvider] = {}


def get_sms_provider(settings: Settings | None = None) -> SmsProvider:
    settings = settings or get_settings()
    provider_name = settings.sms_provider

    if provider_name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider_name]

    from openclear import plugins

    provider: SmsProvider = plugins.lookup("sms_providers", provider_name, what="SMS provider")()

    _PROVIDER_CACHE[provider_name] = provider
    return provider
