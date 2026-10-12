#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Prints an edge network's published address ranges in the form
OPENCLEAR_TRUSTED_PROXY_CIDRS takes (comma-separated), so OpenClear trusts its client-IP
header only on connections from it (WAF_PLAN.md W1; docs/guides/edge.md).

Cloudflare publishes its ranges (https://www.cloudflare.com/ips/); refresh them now and then.
Akamai's Site Shield ranges are specific to each customer: copy them from Akamai Control
Center (Site Shield → your map) instead.

Usage:
    python scripts/edge_ip_ranges.py cloudflare
"""

import json
import sys
import urllib.request

# Cloudflare's public API lists its ranges (no account needed).
CLOUDFLARE = "https://api.cloudflare.com/client/v4/ips"


def main() -> int:
    edge = sys.argv[1] if len(sys.argv) > 1 else ""
    if edge == "akamai":
        print("Akamai's Site Shield ranges are specific to your account: copy them from Akamai Control Center "
              "(Site Shield → your map) into OPENCLEAR_TRUSTED_PROXY_CIDRS, comma-separated.", file=sys.stderr)
        return 1
    if edge != "cloudflare":
        print(__doc__, file=sys.stderr)
        return 2
    request = urllib.request.Request(CLOUDFLARE, headers={"User-Agent": "OpenClear edge_ip_ranges.py", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 -- a fixed https URL
        result = json.load(response)["result"]
    ranges = result["ipv4_cidrs"] + result["ipv6_cidrs"]
    print("OPENCLEAR_TRUSTED_PROXY_CIDRS=" + ",".join(ranges))
    return 0


if __name__ == "__main__":
    sys.exit(main())
