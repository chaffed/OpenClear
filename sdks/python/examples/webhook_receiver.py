# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""A minimal webhook receiver (standard library only): verifies each event, skips one it has
already seen, and fetches the exception's details through the API.

    OPENCLEAR_WEBHOOK_SECRET=whsec_... OPENCLEAR_URL=... OPENCLEAR_CLIENT_ID=... OPENCLEAR_CLIENT_SECRET=... \\
        python webhook_receiver.py
"""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from openclear_sdk import OpenClear, WebhookVerificationError, webhooks

SECRET = os.environ["OPENCLEAR_WEBHOOK_SECRET"]
oc = OpenClear(os.environ["OPENCLEAR_URL"], client_id=os.environ["OPENCLEAR_CLIENT_ID"], client_secret=os.environ["OPENCLEAR_CLIENT_SECRET"])
seen: set[str] = set()  # use your database in production


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            event = webhooks.verify(SECRET, dict(self.headers), body)
        except WebhookVerificationError:
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(204)  # answer quickly; OpenClear retries anything else
        self.end_headers()
        message_id = self.headers["webhook-id"]
        if message_id in seen:
            return
        seen.add(message_id)
        if event["type"] == "exception.created":
            exception = oc.exceptions.get_exception(event["data"]["exception_id"])
            print("New exception", exception["id"], exception.get("exception_type"))


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Receiver).serve_forever()
