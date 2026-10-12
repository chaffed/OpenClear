# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A stand-in for an edge network in front of OpenClear (WAF_PLAN.md, "A stub edge first"):
a small proxy that behaves like Cloudflare or Akamai, so the edge check
(src/openclear/edge_check.py) is tested without a real zone. It adds the edge's client-IP
header, its request id and the edge secret; caches static files by extension; compresses;
limits body sizes; and gives up on slow origins. `faults` switch on the mistakes real edge
setups make, so the check can be shown to catch each one:

- "no_client_ip": the client-IP header isn't sent (or OpenClear isn't told to trust it);
- "inject_scripts": Rocket Loader-style scripts added to pages;
- "add_security_headers": the edge's own security headers on top of OpenClear's;
- "cache_everything": every GET cached, pages and API included;
- "waf_markup": a WAF rule blocking bodies with HTML-like text;
- "waf_saml": a WAF rule blocking SAML responses;
- "rate_limit_api": the edge's rate limit on /api after a few calls;
- "no_compression": responses sent as they came."""

import gzip
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus

import httpx

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
        "content-length", "content-encoding", "host"}
_STATIC = (".css", ".js", ".svg", ".png", ".ico", ".woff2")
_BLOCK_PAGE = b"<!doctype html><title>Attention Required!</title><h1>Sorry, you have been blocked</h1>"


class StubEdge:
    def __init__(self, origin: str, *, kind: str = "cloudflare", client_ip: str = "203.0.113.7", secret: str | None = None,
                 secret_header: str = "x-edge-secret", faults: set[str] | None = None, max_body: int = 100 * 1024 * 1024,
                 origin_timeout: float | None = None) -> None:
        self.origin, self.kind, self.client_ip = origin.rstrip("/"), kind, client_ip
        self.secret, self.secret_header, self.faults, self.max_body = secret, secret_header, set(faults or ()), max_body
        self.timeout = origin_timeout or (100.0 if kind == "cloudflare" else 120.0)
        self.cache: dict[str, tuple[int, list, bytes]] = {}
        self.api_calls = 0
        self.lock = threading.Lock()
        edge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _handle(self):
                edge.serve(self)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()

    # --- The edge's own answers (no X-Request-ID: they never reached OpenClear) ----------------

    def _reply(self, handler, status: int, body: bytes, headers: dict | None = None) -> None:
        handler.send_response(status)
        for name, value in {**self._edge_headers(), "content-type": "text/html", **(headers or {})}.items():
            handler.send_header(name, value)
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _edge_headers(self) -> dict:
        if self.kind == "cloudflare":
            return {"server": "cloudflare", "cf-ray": uuid.uuid4().hex[:16] + "-IAD"}
        return {"server": "AkamaiGHost", "x-akamai-request-id": uuid.uuid4().hex[:8]}

    # --- Proxying -------------------------------------------------------------------------------

    def serve(self, handler) -> None:
        length = int(handler.headers.get("content-length") or 0)
        if length > self.max_body:
            self._reply(handler, 413, b"<h1>413 Request Entity Too Large</h1>")
            return
        body = handler.rfile.read(length) if length else b""
        path = handler.path
        # A WAF reads a form body decoded, as the application will.
        if "waf_markup" in self.faults and "<script" in unquote_plus(body.decode("latin-1")).lower():
            self._reply(handler, 403, _BLOCK_PAGE)
            return
        if "waf_saml" in self.faults and path.endswith("/acs"):
            self._reply(handler, 403, _BLOCK_PAGE)
            return
        if path.startswith("/api/"):
            with self.lock:
                self.api_calls += 1
                calls = self.api_calls
            if "rate_limit_api" in self.faults and calls > 5:
                self._reply(handler, 429, b"<h1>Rate limited</h1>")
                return

        cacheable = handler.command == "GET" and ("cache_everything" in self.faults or path.split("?")[0].endswith(_STATIC))
        if cacheable and path in self.cache:
            status, headers, content = self.cache[path]
            self._send(handler, status, headers, content, cache="HIT")
            return

        headers = {k: v for k, v in handler.headers.items() if k.lower() not in _HOP}
        if "no_client_ip" not in self.faults:
            headers["CF-Connecting-IP" if self.kind == "cloudflare" else "True-Client-IP"] = self.client_ip
        headers.update({"CF-Ray": uuid.uuid4().hex[:16] + "-IAD"} if self.kind == "cloudflare"
                       else {"X-Akamai-Request-ID": uuid.uuid4().hex[:8], "Akamai-Origin-Hop": "1"})
        if self.secret:
            headers[self.secret_header] = self.secret
        try:
            response = httpx.request(handler.command, self.origin + path, headers=headers, content=body, timeout=self.timeout,
                                     follow_redirects=False)
        except httpx.TimeoutException:
            self._reply(handler, 524 if self.kind == "cloudflare" else 504, b"<h1>The origin didn't answer in time</h1>")
            return
        except httpx.HTTPError:
            self._reply(handler, 502, b"<h1>Bad gateway</h1>")
            return
        out_headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in _HOP]
        content = response.content
        if "inject_scripts" in self.faults and "text/html" in response.headers.get("content-type", ""):
            content = content.replace(b"</body>", b'<script src="/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"></script></body>')
        if "add_security_headers" in self.faults:
            out_headers = [(k, v) for k, v in out_headers if k.lower() != "x-frame-options"]
            out_headers += [("x-frame-options", "SAMEORIGIN"), ("content-security-policy", "upgrade-insecure-requests")]
        if cacheable and response.status_code == 200:
            self.cache[path] = (response.status_code, out_headers, content)
        self._send(handler, response.status_code, out_headers, content, cache="MISS" if cacheable else "DYNAMIC",
                   accept_encoding=handler.headers.get("accept-encoding", ""))

    def _send(self, handler, status: int, headers: list, content: bytes, *, cache: str, accept_encoding: str = "gzip") -> None:
        compress = ("no_compression" not in self.faults and "gzip" in accept_encoding and len(content) > 256
                    and any(t in dict(headers).get("content-type", "") for t in ("text/", "json", "javascript")))
        if compress:
            content = gzip.compress(content)
        handler.send_response(status)
        for name, value in headers:
            handler.send_header(name, value)
        for name, value in self._edge_headers().items():
            handler.send_header(name, value)
        handler.send_header("cf-cache-status" if self.kind == "cloudflare" else "x-cache", cache if self.kind == "cloudflare"
                            else {"HIT": "TCP_HIT", "MISS": "TCP_MISS"}.get(cache, "TCP_MISS"))
        if compress:
            handler.send_header("content-encoding", "gzip")
        handler.send_header("content-length", str(len(content)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(content)
