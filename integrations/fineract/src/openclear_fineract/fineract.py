# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""The few Apache Fineract REST calls the adapter needs (Fineract 1.x, /fineract-provider/api/v1)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx


class FineractError(Exception):
    pass


class Fineract:
    def __init__(self, base_url: str, *, username: str, password: str, tenant: str = "default",
                 http_client: httpx.Client | None = None, page_size: int = 200) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=30)
        self._auth = (username, password)
        self._headers = {"Fineract-Platform-TenantId": tenant, "Accept": "application/json"}
        self.page_size = page_size

    def _request(self, method: str, path: str, **kwargs) -> Any:
        response = self._http.request(method, self.base_url + path, auth=self._auth, headers=self._headers, **kwargs)
        if response.status_code >= 400:
            raise FineractError(f"{method} {path}: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def _paged(self, path: str, params: dict | None = None) -> Iterator[dict]:
        offset = 0
        while True:
            page = self._request("GET", path, params={**(params or {}), "offset": offset, "limit": self.page_size, "paged": "true"})
            items = page.get("pageItems", []) if isinstance(page, dict) else page
            yield from items
            offset += len(items)
            total = page.get("totalFilteredRecords", offset) if isinstance(page, dict) else offset
            if not items or offset >= total:
                return

    def clients(self) -> Iterator[dict]:
        return self._paged("/clients")

    def savings_accounts(self) -> Iterator[dict]:
        return self._paged("/savingsaccounts")

    def savings_transactions(self, account_id: int) -> list[dict]:
        account = self._request("GET", f"/savingsaccounts/{account_id}", params={"associations": "transactions"})
        return account.get("transactions", []) or []

    def undo_transaction(self, account_id: int, transaction_id: int) -> None:
        self._request("POST", f"/savingsaccounts/{account_id}/transactions/{transaction_id}", params={"command": "undo"}, json={})
