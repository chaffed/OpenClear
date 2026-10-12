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

Exits 1 if any check fails.
"""

import argparse
import sys
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
    args = parser.parse_args()

    from openclear import edge_check

    checks = edge_check.run(args.url, origin_url=args.origin, expect_ip=args.expect_ip, burst=args.burst, verify=not args.insecure)
    print(edge_check.as_json(checks) if args.json else edge_check.report(checks))
    return 1 if edge_check.failed(checks) else 0


if __name__ == "__main__":
    sys.exit(main())
