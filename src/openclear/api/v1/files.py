# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Route options for endpoints that return a file rather than JSON, so the API contract
says so and generated SDKs return the file's bytes instead of parsing it."""

from fastapi.responses import Response


def file_response(media_type: str, description: str) -> dict:
    return {
        "response_class": Response,
        "responses": {200: {"description": description, "content": {media_type: {"schema": {"type": "string", "format": "binary"}}}}},
    }


CSV_FILE = file_response("text/csv", "A CSV file")
XML_FILE = file_response("application/xml", "An XML file")
ZIP_FILE = file_response("application/zip", "A zip archive")
# A decision file's type depends on its layout (plugins.api.DecisionLayout.media_type).
ANY_FILE = file_response("application/octet-stream", "A file; its Content-Type depends on the layout")
