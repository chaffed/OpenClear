# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""Sending many items at once. `submit` splits a large list into batches, sends each to the
bulk endpoint, and when OpenClear answers a batch with 202 (too large to import during
the call) waits for its background import. Either way you get one result per item, in the
order you sent them."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from openclear_sdk.errors import BulkUploadError

if TYPE_CHECKING:
    from openclear_sdk.client import OpenClear

BulkKind = Literal["issued_items", "paid_items", "ach_transactions"]
_FINISHED = {"completed", "rejected", "failed"}


@dataclass
class BulkResult:
    """Every item's outcome: `results[i]` is for the i-th item sent (`index` == i), with
    `success`, the created `id`, and `error`."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    upload_ids: list[str] = field(default_factory=list)  # batches imported in the background

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if not r.get("success")]


class Bulk:
    def __init__(self, client: OpenClear) -> None:
        self._c = client
        self.poll_interval = 2.0

    def _endpoint(self, kind: BulkKind):
        return {
            "issued_items": self._c.issued_items.create_issued_items_bulk,
            "paid_items": self._c.paid_items.create_paid_items_bulk,
            "ach_transactions": self._c.ach_transactions.create_ach_transactions_bulk,
        }[kind]

    def submit(self, kind: BulkKind, items: Iterable[dict[str, Any]], *, batch_size: int = 1000, timeout: float = 3600) -> BulkResult:
        """Creates every item. One bad item fails only itself (see `failures`)."""
        send = self._endpoint(kind)
        outcome = BulkResult()
        batch: list[dict[str, Any]] = []
        offset = 0

        def flush() -> None:
            nonlocal offset, batch
            if not batch:
                return
            answer = send(batch)
            if "upload_id" in answer:
                upload = self.wait(answer["upload_id"], timeout=timeout)
                outcome.upload_ids.append(answer["upload_id"])
                rows = list(self._c.bulk_uploads.iter_get_bulk_upload_results(answer["upload_id"]))
                if upload["status"] != "completed" and len(rows) < len(batch):
                    raise self._failure(upload)
            else:
                rows = answer["results"]
            for row in rows:
                outcome.results.append({**row, "index": int(row["index"]) + offset})
            offset += len(batch)
            batch = []

        for item in items:
            batch.append(item)
            if len(batch) >= batch_size:
                flush()
        flush()
        outcome.results.sort(key=lambda r: r["index"])
        outcome.total = len(outcome.results)
        outcome.succeeded = sum(1 for r in outcome.results if r.get("success"))
        outcome.failed = outcome.total - outcome.succeeded
        return outcome

    def upload_file(
        self, kind: str, format: str, file: Any, *, create_missing_accounts: bool = False, wait: bool = True, timeout: float = 3600,
    ) -> dict[str, Any]:
        """Uploads a file in any import format the bank can use (`bulk_uploads.list_import_formats`)
        and, unless `wait=False`, waits for its import. Returns the upload's final status;
        `bulk_uploads.iter_get_bulk_upload_results(upload["id"])` gives each row. Raises
        BulkUploadError if the file was rejected or the import failed."""
        accepted = self._c.bulk_uploads.upload_file(file, format, kind, create_missing_accounts=create_missing_accounts)
        if not wait:
            return self._c.bulk_uploads.get_bulk_upload(accepted["upload_id"])
        upload = self.wait(accepted["upload_id"], timeout=timeout)
        if upload["status"] != "completed":
            raise self._failure(upload)
        return upload

    def wait(self, upload_id: str, *, timeout: float = 3600) -> dict[str, Any]:
        """Polls an upload until it's completed, rejected or failed."""
        deadline = time.monotonic() + timeout
        while True:
            upload = self._c.bulk_uploads.get_bulk_upload(upload_id)
            if upload["status"] in _FINISHED:
                return upload
            if time.monotonic() >= deadline:
                raise BulkUploadError(f"Upload {upload_id} still {upload['status']} after {timeout:.0f}s.")
            self._c._sleep(self.poll_interval)

    @staticmethod
    def _failure(upload: dict[str, Any]) -> BulkUploadError:
        error = BulkUploadError(f"Upload {upload['id']} {upload['status']}: {upload.get('error') or 'see its row results'}")
        error.upload = upload
        return error
