# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""What a plugin imports from OpenClear (docs/PLUGINS.md). These names are kept stable
within a PLUGIN_API_VERSION; anything else in OpenClear may change in any release.

    from openclear.plugins.api import DecisionLayout, ImportSpec, LoadedFile, SourceRow
"""

from collections.abc import Callable
from dataclasses import dataclass

from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.plugins import PLUGIN_API_VERSION, PluginError, Registry
from openclear.services.bulk_import_service import (
    BulkFileRejected,
    ImportSpec,
    LoadedFile,
    RowContext,
    SourceRow,
)
from openclear.services.bulk_import_service import _read as _read_stored_file


def read_stored_file(ref: str) -> bytes:
    """The bytes of an uploaded file, given the reference an ImportSpec's `load` receives."""
    return _read_stored_file(ref)


def builtin_import_format(kind: BulkUploadKind, file_format: str) -> ImportSpec:
    """One of OpenClear's own import formats, so a plugin that only reads a different file
    layout can reuse its row handling: parse the file into the rows the built-in
    expects, and pass them to its `ingest`."""
    from openclear.services.bulk_import_service import BUILTIN_SPECS

    return BUILTIN_SPECS[(kind, file_format)]


@dataclass(frozen=True)
class DecisionLayout:
    """A decision file a core takes back. `write` receives decisions as the decision feed
    returns them (GET /api/v1/decisions; services/decision_feed_service.read_feed), oldest
    first, and returns the file's contents."""

    label: str  # shown to people, e.g. "Fineract repayment reversal CSV"
    media_type: str  # e.g. "text/csv"
    extension: str  # e.g. "csv", without the dot
    write: Callable[[list[dict]], bytes]


__all__ = [
    "PLUGIN_API_VERSION", "BulkFileRejected", "BulkUploadKind", "DecisionLayout", "ImportSpec", "LoadedFile", "PluginError",
    "Registry", "RowContext", "SourceRow", "builtin_import_format", "read_stored_file",
]
