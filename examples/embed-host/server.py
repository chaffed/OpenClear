# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""A stand-in for a bank's online banking: one page that shows OpenClear embedded for a
signed-in person (docs/guides/embedding.md). Standard library plus openclear-sdk."""

import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from openclear_sdk import OpenClear

HERE = Path(__file__).resolve().parent
EMBED_JS = HERE.parents[1] / "sdks" / "embed" / "dist" / "index.js"
PORT = int(os.environ.get("PORT", "8443"))
ORIGIN = os.environ.get("ORIGIN", f"https://localhost:{PORT}")

oc = OpenClear(os.environ["OPENCLEAR_URL"], client_id=os.environ["OPENCLEAR_CLIENT_ID"], client_secret=os.environ["OPENCLEAR_CLIENT_SECRET"])

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Acme Bank online banking</title>
<style>body{font-family:system-ui;margin:0}header{background:#123;color:#fff;padding:1rem}main{padding:1rem}</style></head>
<body><header>Acme Bank · Business online banking</header>
<main><h1>Positive pay</h1><div id="positive-pay"></div><p id="last-event"></p></main>
<script type="module">
import { mount } from "/embed.js";
const launch = async () => (await (await fetch("/launch", { method: "POST" })).json()).url;
const openclear = mount(document.getElementById("positive-pay"), {
  url: await launch(),
  onEvent: (e) => { if (e.type !== "resize") document.getElementById("last-event").textContent = "OpenClear: " + e.type; },
  onSessionEnded: async () => openclear.navigate(await launch()),
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/embed.js":
            self._send(200, EMBED_JS.read_bytes(), "text/javascript")
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self):  # noqa: N802
        if self.path != "/launch":
            return self._send(404, b"", "text/plain")
        # In real online banking: oc.for_person(the signed-in person's ID token).
        person = oc.acting_as(os.environ["PERSON_EMAIL"])
        launch = person.embed.create_launch({"parent_origin": ORIGIN, "path": "/ui/exceptions"})
        self._send(200, json.dumps({"url": launch["url"]}).encode(), "application/json")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    if os.environ.get("CERT"):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(os.environ["CERT"], os.environ["KEY"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Serving {ORIGIN}")
    server.serve_forever()
