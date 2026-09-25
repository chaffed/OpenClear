# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.networks.base import NetworkAdapter

_ADAPTERS: dict[str, NetworkAdapter] = {}


def register_adapter(adapter: NetworkAdapter) -> None:
    _ADAPTERS[adapter.code] = adapter


def get_adapter(network_code: str) -> NetworkAdapter:
    try:
        return _ADAPTERS[network_code]
    except KeyError:
        raise ValueError(
            f"No network adapter registered for code={network_code!r}. "
            f"Registered: {sorted(_ADAPTERS)}. Is its package imported at app startup?"
        ) from None


def registered_codes() -> list[str]:
    return sorted(_ADAPTERS)


def reset_registry() -> None:
    """Test-only: clear registrations between test modules that register fakes."""
    _ADAPTERS.clear()
