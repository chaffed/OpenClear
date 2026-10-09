# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""The OpenClear client: authentication, retries, paging, and the API areas.

    from openclear_sdk import OpenClear

    with OpenClear("https://openclear.example.com", client_id="occ_...", client_secret="...") as oc:
        oc.accounts.upsert_account("12345678", {"name": "Operating"})
        for item in oc.issued_items.iter_issued_items():
            ...
"""

from __future__ import annotations

import copy
import random
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from openclear_sdk import _generated as g
from openclear_sdk.errors import APIConnectionError, AuthenticationError, OpenClearError, RateLimitError, error_for

__all__ = ["OpenClear", "Transport"]

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_RETRY_STATUSES = {429, 502, 503, 504}
_TOKEN_MARGIN_SECONDS = 60


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openclear-sdk")
    except PackageNotFoundError:
        return "source"


class Transport:
    """What the generated endpoint classes call. OpenClear is the one implementation."""

    def call(self, method: str, path: str, **options: Any) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def paginate(self, method: str, path: str, **options: Any) -> Iterator[Any]:  # pragma: no cover - interface
        raise NotImplementedError


class _Token:
    """The client-credentials access token, shared by a client and the copies `acting_as`
    makes, and refreshed shortly before it expires."""

    def __init__(self, fixed: str | None = None) -> None:
        self.value = fixed
        self.expires_at = float("inf") if fixed else 0.0
        self.fixed = fixed is not None
        self.lock = threading.Lock()


class OpenClear(Transport):
    """A connection to one OpenClear installation, as one API client.

    Authenticate with an API client's `client_id` and `client_secret` (Admin → API
    clients); the token is fetched and refreshed for you. Or pass an `access_token` you
    already have, or a `platform_api_key` for the platform endpoints.

    Every write gets an `Idempotency-Key`, kept across retries, so a retried call never
    acts twice. Connection failures, 429 and 502–504 are retried up to `max_retries` times
    with backoff (honouring `Retry-After`)."""

    accounts: g.AccountsApi
    ach_authorizations: g.AchAuthorizationsApi
    ach_transactions: g.AchTransactionsApi
    admin: g.AdminApi
    api_clients: g.ApiClientsApi
    billing: g.BillingApi
    bulk_uploads: g.BulkUploadsApi
    check_images: g.CheckImagesApi
    customers: g.CustomersApi
    decision_feed: DecisionFeedApi
    decisions: g.DecisionsApi
    dropbox_import: g.DropboxImportApi
    embed: g.EmbedApi
    exceptions: g.ExceptionsApi
    fraud_training: g.FraudTrainingApi
    issued_items: g.IssuedItemsApi
    paid_items: g.PaidItemsApi
    platform_billing: g.PlatformBillingApi
    platform_jobs: g.PlatformJobsApi
    platform_ml: g.PlatformMlApi
    platform_usage: g.PlatformUsageApi
    stop_payments: g.StopPaymentsApi
    users: g.UsersApi

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        platform_api_key: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
        user_agent: str | None = None,
    ) -> None:
        if client_id and not client_secret:
            raise ValueError("client_secret is needed with client_id.")
        self.base_url = base_url.rstrip("/")
        self._client_id, self._client_secret = client_id, client_secret
        self._platform_api_key = platform_api_key
        self._token = _Token(access_token)
        self.max_retries = max_retries
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._headers = {"User-Agent": user_agent or f"openclear-sdk-python/{_version()}", "Accept": "application/json"}
        self._sleep: Callable[[float], None] = time.sleep
        self._bind_areas()
        from openclear_sdk.bulk import Bulk

        self.bulk = Bulk(self)

    def _bind_areas(self) -> None:
        for name, cls in {**g.AREAS, "decision_feed": DecisionFeedApi}.items():
            setattr(self, name, cls(self))

    # --- Lifecycle ---------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> OpenClear:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def acting_as(self, email: str, *, customer_number: str | None = None) -> OpenClear:
        """A client that acts for one of the bank's people (`OpenClear-Act-As`). Only for an
        API client the bank allowed to act for users; what it may do is what both the
        client and the person may do, and the action is recorded as theirs."""
        other = copy.copy(self)
        other._headers = {**self._headers, "OpenClear-Act-As": email}
        if customer_number:
            other._headers["OpenClear-Act-As-Customer"] = customer_number
        other._owns_http = False
        other._bind_areas()
        from openclear_sdk.bulk import Bulk

        other.bulk = Bulk(other)
        return other

    def acting_as_subject(self, subject: str) -> OpenClear:
        """Like `acting_as`, naming the person by your online banking platform's own id for
        them (`OpenClear-Act-As-Subject`). Needs the client linked to your sign-on
        connection, and the person to have signed in (or been exchanged) once."""
        other = self.acting_as("")
        other._headers.pop("OpenClear-Act-As", None)
        other._headers["OpenClear-Act-As-Subject"] = subject
        return other

    def for_person(self, id_token: str) -> OpenClear:
        """Token exchange (RFC 8693): trades the ID token of a person signed in to your online
        banking platform for a client that acts for them, with what both they and this
        client may do. It expires when the ID token does; exchange again after that."""
        response = self._send(
            "POST", "/api/v1/oauth/token", auth=None, idempotent=False,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": self._client_id,
                "client_secret": self._client_secret, "subject_token": id_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
            },
        )
        other = copy.copy(self)
        other._token = _Token(response.json()["access_token"])
        other._owns_http = False
        other._bind_areas()
        from openclear_sdk.bulk import Bulk

        other.bulk = Bulk(other)
        return other

    # --- Authentication ----------------------------------------------------------------

    def _bearer(self, *, refresh: bool = False) -> str:
        token = self._token
        if token.fixed:
            if not token.value:
                raise AuthenticationError("No access token.")
            return token.value
        if not self._client_id:
            raise AuthenticationError("Pass client_id and client_secret, or an access_token.")
        with token.lock:
            if refresh or not token.value or time.time() >= token.expires_at - _TOKEN_MARGIN_SECONDS:
                response = self._send(
                    "POST", "/api/v1/oauth/token", auth=None, idempotent=False,
                    data={"grant_type": "client_credentials", "client_id": self._client_id, "client_secret": self._client_secret},
                )
                body = response.json()
                token.value = body["access_token"]
                token.expires_at = time.time() + float(body.get("expires_in", 1800))
            return token.value  # type: ignore[return-value]

    def _auth_headers(self, auth: str | None, *, refresh: bool = False) -> dict[str, str]:
        if auth is None:
            return {}
        if auth == "platform":
            if not self._platform_api_key:
                raise AuthenticationError("This is a platform endpoint: pass platform_api_key.")
            return {"X-Api-Key": self._platform_api_key}
        return {"Authorization": f"Bearer {self._bearer(refresh=refresh)}"}

    # --- Sending -----------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            # A Link header's next page: keep only its path and query, so a proxy in front
            # of OpenClear can't send the next request somewhere else.
            parts = urlsplit(path)
            path = parts.path + (f"?{parts.query}" if parts.query else "")
        return self.base_url + path

    @staticmethod
    def _query(query: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not query:
            return None
        out: dict[str, Any] = {}
        for key, value in query.items():
            if value is None:
                continue
            out[key] = ("true" if value else "false") if isinstance(value, bool) else value
        return out

    @staticmethod
    def _form(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not data:
            return None
        return {k: ("true" if v else "false") if isinstance(v, bool) else str(v) for k, v in data.items() if v is not None}

    @staticmethod
    def _files(files: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Files read into memory first, so a retry can send them again."""
        if not files:
            return None
        out = {}
        for key, value in files.items():
            if value is None:
                continue
            if isinstance(value, tuple):
                name, content, *rest = value
                content = content if isinstance(content, bytes) else content.read()
                out[key] = (name, content, *rest)
            elif isinstance(value, bytes):
                out[key] = (key, value)
            else:
                out[key] = (getattr(value, "name", key).rsplit("/", 1)[-1], value.read())
        return out

    def _send(
        self, method: str, path: str, *, query: Mapping[str, Any] | None = None, json: Any = None,
        data: Mapping[str, Any] | None = None, files: Mapping[str, Any] | None = None, auth: str | None = "bearer",
        idempotent: bool = True,
    ) -> httpx.Response:
        headers = dict(self._headers)
        if method in _WRITE_METHODS and idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        files = self._files(files)
        form = self._form(data)
        refreshed = False
        attempt = 0
        while True:
            try:
                request_headers = {**headers, **self._auth_headers(auth, refresh=False)}
                response = self._http.request(
                    method, self._url(path), params=self._query(query), json=json, data=form, files=files, headers=request_headers,
                )
            except httpx.TransportError as exc:
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise APIConnectionError(f"Couldn't reach OpenClear: {exc}") from exc
            if response.status_code == 401 and auth == "bearer" and not self._token.fixed and not refreshed:
                self._bearer(refresh=True)  # the token was revoked or expired early: get a new one, once
                refreshed = True
                continue
            # A retried write whose first attempt is still running gets 409; wait for it.
            in_flight = response.status_code == 409 and attempt > 0 and "Idempotency-Key" in headers
            if (response.status_code in _RETRY_STATUSES or in_flight) and attempt < self.max_retries:
                self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                attempt += 1
                continue
            if response.status_code >= 400:
                raise self._error(response)
            return response

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(0.5 * 2**attempt, 8.0) * (0.5 + random.random() / 2)

    @staticmethod
    def _error(response: httpx.Response) -> OpenClearError:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        error = error_for(response.status_code, body)
        if isinstance(error, RateLimitError):
            try:
                error.retry_after = float(response.headers.get("retry-after", ""))
            except ValueError:
                error.retry_after = None
        return error

    @staticmethod
    def _decode(response: httpx.Response, raw: bool) -> Any:
        if raw:
            return response.content
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- What the generated endpoints call ---------------------------------------------

    def call(
        self, method: str, path: str, *, query: Mapping[str, Any] | None = None, json: Any = None,
        data: Mapping[str, Any] | None = None, files: Mapping[str, Any] | None = None,
        auth: Literal["bearer", "platform"] = "bearer", raw: bool = False,
    ) -> Any:
        return self._decode(self._send(method, path, query=query, json=json, data=data, files=files, auth=auth), raw)

    def call_with_response(self, method: str, path: str, **options: Any) -> tuple[Any, httpx.Response]:
        """Like `call`, also returning the response (for its headers)."""
        raw = options.pop("raw", False)
        response = self._send(method, path, **options)
        return self._decode(response, raw), response

    def paginate(
        self, method: str, path: str, *, query: Mapping[str, Any] | None = None, auth: Literal["bearer", "platform"] = "bearer",
    ) -> Iterator[Any]:
        response = self._send(method, path, query=query, auth=auth)
        while True:
            yield from response.json() or []
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return
            response = self._send(method, next_url, auth=auth)


class DecisionFeedApi(g.DecisionFeedApi):
    """The decision feed, plus `consume` for the usual core job."""

    def position(self, consumer: str) -> int:
        """The last sequence `consumer` acknowledged (0 if it never has)."""
        for row in self.feed_consumers():
            if row.get("consumer") == consumer:
                return int(row.get("through_sequence") or 0)
        return 0

    def consume(
        self, consumer: str, handle: Callable[[dict[str, Any]], None], *, after: int | None = None, page_size: int = 500,
    ) -> int:
        """Hands every decision after `consumer`'s acknowledged position to `handle`, in
        order, acknowledging after each page. Returns the last sequence handled. If
        `handle` raises, nothing after the last acknowledged page is acknowledged, so the
        next run starts again from there (make `handle` safe to repeat)."""
        position = self.position(consumer) if after is None else after
        while True:
            page = self.decision_feed(after=position, limit=page_size)
            if not page:
                return position
            for decision in page:
                handle(decision)
            position = int(page[-1]["sequence"])
            self.acknowledge({"consumer": consumer, "through_sequence": position})
            if len(page) < page_size:
                return position
