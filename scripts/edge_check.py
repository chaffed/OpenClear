#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Checks an OpenClear deployment through its edge network (Cloudflare, Akamai, a load
balancer): see src/openclear/edge_check.py for what it checks. Needs no account and changes
nothing. Run it at go-live and after every change to the edge's WAF or caching rules.

Usage:
    python scripts/edge_check.py https://openclear.bank.example
    python scripts/edge_check.py https://openclear.bank.example --origin https://10.0.0.5:8443 --expect-ip 203.0.113.7
    python scripts/edge_check.py https://openclear.bank.example --json > edge-check.json

With --crs, the address is an OpenClear that Docker can reach, and the check instead puts the
OWASP Core Rule Set (ModSecurity, the official owasp/modsecurity-crs image) in front of it at
paranoia levels 1 and 2, with docs/guides/crs/openclear-exclusions.conf, and sends the same
legitimate requests through: proof that the exclusions in docs/guides/edge.md are enough, and
that an attack is still stopped. Needs Docker.

    python scripts/edge_check.py http://openclear-zap:8000 --crs --network zapnet

Exits 1 if any check fails.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="the deployment's address, through the edge")
    parser.add_argument("--origin", help="the origin's own address, to check it refuses requests that go around the edge")
    parser.add_argument("--expect-ip", help="this machine's public address, to check OpenClear sees it")
    parser.add_argument("--burst", type=int, default=20, help="how many quick API calls to send (default 20)")
    parser.add_argument("--insecure", action="store_true", help="don't verify TLS certificates (a test origin)")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument("--crs", action="store_true", help="check the WAF exclusions against the real Core Rule Set (needs Docker)")
    parser.add_argument("--network", help="with --crs: the Docker network OpenClear is on")
    parser.add_argument("--no-exclusions", action="store_true", help="with --crs: without OpenClear's exclusions, to see what fires")
    args = parser.parse_args()
    if args.crs:
        return crs(args)

    from openclear import edge_check

    checks = edge_check.run(args.url, origin_url=args.origin, expect_ip=args.expect_ip, burst=args.burst, verify=not args.insecure)
    print(edge_check.as_json(checks) if args.json else edge_check.report(checks))
    return 1 if edge_check.failed(checks) else 0


CRS_IMAGE = os.environ.get("CRS_IMAGE", "owasp/modsecurity-crs@sha256:873ce68a658d2e3b61484a8a24d215f7d15456e7ce5aec5e7114bf000d743365")
EXCLUSIONS = Path(__file__).resolve().parent.parent / "docs" / "guides" / "crs" / "openclear-exclusions.conf"
_ATTACKS = [("A script in a search", "GET", "/ui/login?next=%3Cscript%3Ealert(1)%3C/script%3E", {}),
            ("SQL in a parameter", "GET", "/ui/login?next=1%27%20OR%20%271%27=%271", {})]


def crs(args: argparse.Namespace) -> int:
    """The legitimate probes through CRS at paranoia 1 and 2: each must reach OpenClear, and the
    attacks mustn't."""
    import httpx

    from openclear import edge_check

    failed = False
    for level in (1, 2):
        name = f"openclear-crs-{level}"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        command = ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8080", "-e", f"BACKEND={args.url.rstrip('/')}",
                   "-e", f"PARANOIA={level}", "-e", f"BLOCKING_PARANOIA={level}", "-e", "MODSEC_AUDIT_LOG=/dev/stdout",
                   "-e", "MODSEC_AUDIT_LOG_FORMAT=JSON", "-e", "MODSEC_AUDIT_ENGINE=RelevantOnly",
                   "-e", "MODSEC_REQ_BODY_LIMIT=52428800", "-e", "MODSEC_REQ_BODY_NOFILES_LIMIT=1048576"]
        if args.network:
            command += ["--network", args.network]
        if not args.no_exclusions:
            command += ["-v", f"{EXCLUSIONS}:/etc/modsecurity.d/owasp-crs/rules/REQUEST-900-EXCLUSION-RULES-BEFORE-CRS.conf:ro"]
        subprocess.run(command + [CRS_IMAGE], check=True, capture_output=True)
        try:
            port = subprocess.run(["docker", "port", name, "8080"], check=True, capture_output=True, text=True).stdout.split(":")[-1].strip()
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=30) as client:
                for _ in range(60):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                print(f"Paranoia level {level}{' (no exclusions)' if args.no_exclusions else ''}")
                for probe, method, path, kwargs in edge_check._probes():
                    reached = edge_check._from_origin(client.request(method, path, **kwargs))
                    failed |= not reached
                    print(f"  {'PASS' if reached else 'FAIL'}  {probe}{'' if reached else ': stopped by CRS'}")
                for probe, method, path, kwargs in _ATTACKS:
                    stopped = client.request(method, path, **kwargs).status_code == 403
                    failed |= not stopped
                    print(f"  {'PASS' if stopped else 'FAIL'}  {probe} is stopped")
            fired = _rules_fired(name)
            if fired:
                print("  Rules that fired:", "; ".join(f"{path} {ids}" for path, ids in fired))
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    return 1 if failed else 0


def _rules_fired(container: str) -> list[tuple[str, list[str]]]:
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True).stdout.splitlines()
    out = []
    for line in logs:
        if line.startswith('{"transaction"'):
            transaction = json.loads(line)["transaction"]
            # Not the anomaly totals, nor 920350 (a numeric Host header: only because this test uses 127.0.0.1).
            ids = sorted({m["details"]["ruleId"] for m in transaction.get("messages", [])} - {"949110", "980170", "920350"})
            if ids:
                out.append((transaction["request"]["uri"].split("?")[0], ids))
    return out


if __name__ == "__main__":
    sys.exit(main())
