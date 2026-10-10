# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""`openclear`: the SDK from a shell or a cron job (SDK_PLAN.md P3).

    openclear push paid_items presented.csv
    openclear decisions pull --consumer core --format csv > decisions.csv
    openclear decisions files --download ./outbox
    openclear webhooks tail --secret whsec_...
    openclear audit export --start 2026-01-01 --end 2026-03-31 --format pdf > evidence.pdf
    openclear controls
    openclear events tail --follow --cursor-file .openclear-events

Credentials come from OPENCLEAR_URL, OPENCLEAR_CLIENT_ID and OPENCLEAR_CLIENT_SECRET (or
--url, --client-id, --client-secret). Exit status: 0 done, 1 something failed (rows,
verification), 2 bad usage or credentials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, TextIO

from openclear_sdk.client import OpenClear
from openclear_sdk.errors import BulkUploadError, OpenClearError, WebhookVerificationError
from openclear_sdk import webhooks

CSV_COLUMNS = (
    "sequence", "network", "outcome", "reason_code", "return_transaction_code", "account_number", "account_name", "customer_number",
    "check_number", "trace_number", "amount", "presented_date", "effective_date", "originator_id", "originator_name", "sec_code",
    "source", "decided_by", "decided_at", "decision_id", "exception_id",
)


def _client(args: argparse.Namespace, http_client: Any = None) -> OpenClear:
    url = args.url or os.environ.get("OPENCLEAR_URL")
    client_id = args.client_id or os.environ.get("OPENCLEAR_CLIENT_ID")
    secret = args.client_secret or os.environ.get("OPENCLEAR_CLIENT_SECRET")
    if not (url and client_id and secret):
        raise SystemExit("Set OPENCLEAR_URL, OPENCLEAR_CLIENT_ID and OPENCLEAR_CLIENT_SECRET (or --url, --client-id, --client-secret).")
    return OpenClear(url, client_id=client_id, client_secret=secret, http_client=http_client)


def _push(oc: OpenClear, args: argparse.Namespace, out: TextIO) -> int:
    path = Path(args.file)
    fmt = args.format or ("nacha" if path.suffix.lower() in (".ach", ".nacha") else "x937" if path.suffix.lower() in (".x937", ".icl")
                          else "zip" if path.suffix.lower() == ".zip" else "tabular")
    with path.open("rb") as handle:
        try:
            upload = oc.bulk.upload_file(args.kind, fmt, (path.name, handle.read()), create_missing_accounts=args.create_missing_accounts)
        except BulkUploadError as exc:
            print(f"Rejected: {exc.upload.get('error') if exc.upload else exc}", file=sys.stderr)
            return 1
    print(f"{upload['filename']}: {upload['succeeded']} imported, {upload['failed']} failed (upload {upload['id']})", file=out)
    failures = list(oc.bulk_uploads.iter_get_bulk_upload_results(upload["id"], failures_only=True)) if upload["failed"] else []
    for row in failures:
        print(f"  {row['label']}: {row['error']}", file=out)
    return 1 if failures else 0


def _pull(oc: OpenClear, args: argparse.Namespace, out: TextIO) -> int:
    writer = None
    if args.format == "csv":
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()

    def handle(decision: dict) -> None:
        if writer:
            writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v) for k, v in decision.items()})
        else:
            out.write(json.dumps(decision) + "\n")

    if args.no_ack:
        after = args.after if args.after is not None else oc.decision_feed.position(args.consumer)
        last = after
        for decision in oc.decision_feed.iter_decision_feed(after=after):
            handle(decision)
            last = decision["sequence"]
    else:
        last = oc.decision_feed.consume(args.consumer, handle, after=args.after)
    print(f"Through decision {last}" + ("" if args.no_ack else f" (acknowledged for {args.consumer})"), file=sys.stderr)
    return 0


def _files(oc: OpenClear, args: argparse.Namespace, out: TextIO) -> int:
    target = Path(args.download)
    target.mkdir(parents=True, exist_ok=True)
    failed = 0
    for batch in oc.decision_batches.iter_decision_batches(unacknowledged_only=True):
        content = oc.decision_batches.get_decision_batch_file(batch["id"])
        if hashlib.sha256(content).hexdigest() != batch["sha256"]:
            print(f"{batch['filename']}: SHA-256 doesn't match; not acknowledged", file=sys.stderr)
            failed += 1
            continue
        (target / batch["filename"]).write_bytes(content)
        if not args.no_ack:
            oc.decision_batches.acknowledge_decision_batch(batch["id"])
        print(f"{target / batch['filename']} ({batch['decision_count']} decisions)", file=out)
    return 1 if failed else 0


def _audit_export(oc: OpenClear, args: argparse.Namespace, out: TextIO, *, sleep=None) -> int:
    import time

    sleep = sleep or time.sleep
    content = oc.audit.export(start=args.start, end=args.end, format=args.format)
    if content[:1] == b"{" and b'"status_url"' in content[:2000]:
        # A long period is exported in the background: wait for it, then download it.
        run = json.loads(content)
        print(f"Export queued ({run['id']}); waiting for it...", file=sys.stderr)
        while run["state"] in ("queued", "running"):
            sleep(5)
            run = oc.audit.export_status(run["id"])
        if run["state"] != "done":
            print(f"Export failed: {run.get('error') or run['summary']}", file=sys.stderr)
            return 1
        content = oc.audit.export_file(run["id"])
    if args.output:
        Path(args.output).write_bytes(content)
        print(f"{args.output} ({len(content)} bytes)", file=sys.stderr)
    else:
        stream = getattr(out, "buffer", None)
        stream.write(content) if stream is not None else out.write(content.decode("utf-8", errors="replace"))
    return 0


def _controls(oc: OpenClear, args: argparse.Namespace, out: TextIO) -> int:
    """Each control's status; exit 1 if any fails (for a monitoring check)."""
    result = oc.controls.control_status()
    for control in result["controls"]:
        print(f"{control['status']:4}  {control['title']}: {control['detail']}", file=out)
    print(f"Overall: {result['overall']}", file=out)
    return 1 if result["overall"] == "FAIL" else 0


def _events_tail(oc: OpenClear, args: argparse.Namespace, out: TextIO, *, sleep=None) -> int:
    """Prints the bank's security events as JSON lines, from a cursor, for a log collector
    to pick up (SIEM_PLAN.md S4). With --cursor-file, the position survives restarts; with
    --follow, it keeps polling."""
    import time

    sleep = sleep or time.sleep
    cursor_file = Path(args.cursor_file) if args.cursor_file else None
    after = args.after if args.after is not None else (
        int(cursor_file.read_text().strip() or 0) if cursor_file and cursor_file.exists() else 0)
    while True:
        page = oc.security_events.list_security_events(after=after, limit=500)
        for event in page["events"]:
            out.write(json.dumps(event) + "\n")
        out.flush()
        if page["next_after"] != after:
            after = page["next_after"]
            if cursor_file:
                cursor_file.write_text(str(after))
        if not args.follow:
            return 0
        if len(page["events"]) < 500:
            sleep(args.interval)


def tail_server(secret: str, port: int, out: TextIO) -> HTTPServer:
    """A local webhook receiver that verifies each event and prints it as a JSON line."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            try:
                event = webhooks.verify(secret, dict(self.headers), body)
            except WebhookVerificationError as exc:
                self.send_response(400)
                self.end_headers()
                print(f"rejected: {exc}", file=sys.stderr)
                return
            self.send_response(204)
            self.end_headers()
            out.write(json.dumps({"id": self.headers.get("webhook-id"), **event}) + "\n")
            out.flush()

        def log_message(self, *a):
            pass

    return HTTPServer(("127.0.0.1", port), Handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openclear", description="OpenClear from the command line.")
    parser.add_argument("--url")
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    commands = parser.add_subparsers(dest="command", required=True)

    push = commands.add_parser("push", help="Upload a file to import (paid items, issued items, ACH, accounts, check images)")
    push.add_argument("kind", choices=["paid_items", "issued_items", "ach_transactions", "accounts", "check_images"])
    push.add_argument("file")
    push.add_argument("--format", help="tabular, json, nacha, x937, zip, or a plugin's format (default: from the extension)")
    push.add_argument("--create-missing-accounts", action="store_true")

    decisions = commands.add_parser("decisions", help="Pay and return decisions")
    which = decisions.add_subparsers(dest="decisions_command", required=True)
    pull = which.add_parser("pull", help="New decisions from the feed, then acknowledge them")
    pull.add_argument("--consumer", default="cli", help='Whose position to read and acknowledge (default "cli")')
    pull.add_argument("--after", type=int, help="Start after this sequence instead of the consumer's position")
    pull.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    pull.add_argument("--no-ack", action="store_true", help="Read without acknowledging")
    files = which.add_parser("files", help="Download the outbox's unacknowledged decision files, then acknowledge them")
    files.add_argument("--download", default=".", help="Directory to write them to")
    files.add_argument("--no-ack", action="store_true")

    audit = commands.add_parser("audit", help="Evidence for examiners and auditors")
    audit_which = audit.add_subparsers(dest="audit_command", required=True)
    export = audit_which.add_parser("export", help="A signed evidence bundle for a period (JSON or PDF)")
    export.add_argument("--start", required=True, help="First day, YYYY-MM-DD (UTC)")
    export.add_argument("--end", required=True, help="Last day, inclusive")
    export.add_argument("--format", choices=["json", "pdf"], default="json")
    export.add_argument("--output", "-o", help="Write to this file instead of standard output")

    commands.add_parser("controls", help="Each control's live status (exit 1 if any fails)")

    events = commands.add_parser("events", help="Security events")
    events_which = events.add_subparsers(dest="events_command", required=True)
    events_tail = events_which.add_parser("tail", help="Print the bank's security events as JSON lines")
    events_tail.add_argument("--after", type=int, help="Start after this sequence (default: the cursor file, or the start)")
    events_tail.add_argument("--cursor-file", help="Read and save the position here, so a restart carries on")
    events_tail.add_argument("--follow", action="store_true", help="Keep polling for new events")
    events_tail.add_argument("--interval", type=float, default=15.0, help="Seconds between polls with --follow")

    hooks = commands.add_parser("webhooks", help="Webhooks")
    hook_which = hooks.add_subparsers(dest="webhooks_command", required=True)
    tail = hook_which.add_parser("tail", help="Receive webhooks locally (behind a tunnel), verify and print them")
    tail.add_argument("--secret", default=os.environ.get("OPENCLEAR_WEBHOOK_SECRET"), help="The endpoint's whsec_ secret")
    tail.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: list[str] | None = None, *, http_client: Any = None, out: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    if args.command == "webhooks":
        if not args.secret:
            print("Pass --secret or set OPENCLEAR_WEBHOOK_SECRET.", file=sys.stderr)
            return 2
        server = tail_server(args.secret, args.port, out)
        print(f"Listening on http://127.0.0.1:{args.port}/ (Ctrl-C to stop)", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    try:
        oc = _client(args, http_client)
        if args.command == "push":
            return _push(oc, args, out)
        if args.command == "audit":
            return _audit_export(oc, args, out)
        if args.command == "controls":
            return _controls(oc, args, out)
        if args.command == "events":
            return _events_tail(oc, args, out)
        if args.decisions_command == "pull":
            return _pull(oc, args, out)
        return _files(oc, args, out)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    except OpenClearError as exc:
        print(f"OpenClear: {exc}", file=sys.stderr)
        return 2 if exc.status_code in (401, 403) else 1


if __name__ == "__main__":
    sys.exit(main())

