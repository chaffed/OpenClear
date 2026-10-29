# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""OWASP ZAP against a running OpenClear (OWASP_PLAN.md OW-D8), signed in as a bank
administrator and as a customer user of the bank scripts/zap_seed.py creates.

    python scripts/zap_scan.py --sign-in-url http://localhost:8000 --target http://openclear-zap:8000 \\
        --network zapnet --password "$ZAP_PASSWORD" [--active]

- **Baseline** (every pull request): ZAP's spider and passive rules over the web pages, once
  per user. Forms aren't posted, so the scan can't sign itself out or change anything. Fails
  on any alert of medium risk or worse that zap-rules.tsv doesn't waive (with a reason).
- **--active** (weekly, and before a release): ZAP's active scan of the web pages as each
  user, and of the API from openapi/openclear-v1.json. Never fails; reports what it found.

What's printed is each alert's rule, name and count, never the requests (which carry
sessions) or the pages. Reports stay in .zap/ on the machine that ran the scan."""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".zap"
RULES = ROOT / "zap-rules.tsv"
IMAGE = os.environ.get("ZAP_IMAGE", "ghcr.io/zaproxy/zaproxy:stable")
SLUG = "zap-bank"
USERS = {"bank-admin": "admin@zap-bank.example.com", "customer-user": "pat@zap-customer.example.com"}
RISKS = {0: "informational", 1: "low", 2: "medium", 3: "high"}


def sign_in(base_url: str, email: str, password: str) -> str:
    """The Cookie header of a web session for this person."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = opener.open(f"{base_url}/ui/login").read().decode()
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    data = urllib.parse.urlencode({"tenant_slug": SLUG, "email": email, "password": password, "csrf_token": token,
                                   "next": "/ui/"}).encode()
    opener.open(f"{base_url}/ui/login", data=data)
    cookies = {c.name: c.value for c in jar}
    if not any(name.endswith("access_token") for name in cookies):
        sys.exit(f"Couldn't sign in as {email}")
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def api_token(base_url: str, email: str, password: str) -> str:
    request = urllib.request.Request(f"{base_url}/api/v1/auth/login", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"tenant_slug": SLUG, "email": email, "password": password}).encode())
    return json.loads(urllib.request.urlopen(request).read())["access_token"]


def zap(args: argparse.Namespace, script: str, report: str, extra: list[str], header: tuple[str, str]) -> None:
    """One of ZAP's packaged scans, sending `header` (the session) on every request, through ZAP's
    ZAP_AUTH_HEADER variables; passed by name, so the value isn't on the command line."""
    env = {**os.environ, "ZAP_AUTH_HEADER": header[0], "ZAP_AUTH_HEADER_VALUE": header[1]}
    command = ["docker", "run", "--rm", "-v", f"{WORK}:/zap/wrk:rw", "-u", "0", "-e", "ZAP_AUTH_HEADER", "-e", "ZAP_AUTH_HEADER_VALUE"]
    if args.network:
        command += ["--network", args.network]
    command += [IMAGE, script, *extra, "-J", report, "-z", "-config spider.postform=false -config spider.processform=false"]
    print("·", script, report)
    subprocess.run(command, check=False, env=env, stdout=None if args.verbose else subprocess.DEVNULL)


def waived() -> dict[tuple[str, str], tuple[str, str | None]]:
    """zap-rules.tsv: rule id, the alert's name (or * for any from that rule), why it's accepted,
    and optionally a regular expression for the only URLs it's accepted on."""
    out = {}
    for row in csv.reader((line for line in RULES.read_text().splitlines() if line.strip() and not line.startswith("#")), delimiter="\t"):
        if len(row) < 3 or not row[2].strip():
            sys.exit(f"zap-rules.tsv: {row[0]} needs a rule id, an alert name (or *) and a reason")
        out[(row[0].strip(), row[1].strip())] = (row[2].strip(), (row[3].strip() or None) if len(row) > 3 else None)
    return out


def _waiver(accepted: dict, alert: dict) -> str | None:
    """Why the alert is accepted, or None. A waiver limited to some URLs covers the alert only if
    every place ZAP found it is one of them."""
    rule, name = str(alert.get("pluginid")), alert.get("name")
    found = accepted.get((rule, name)) or accepted.get((rule, "*"))
    if found is None:
        return None
    reason, urls = found
    if urls and not all(re.search(urls, i.get("uri", "")) for i in alert.get("instances", [])):
        return None
    return reason


def alerts(report: Path) -> list[dict]:
    if not report.exists():
        sys.exit(f"ZAP wrote no report at {report}: the scan didn't run")
    data = json.loads(report.read_text())
    return [alert for site in data.get("site", []) for alert in site.get("alerts", [])]


def _summary(failing: list[tuple]) -> str:
    """The text of the weekly scan's issue. High-risk alerts are only counted: the issue is
    public, and they're handled privately under SECURITY.md until fixed (OW-D5)."""
    high = [f for f in failing if f[3] >= 3]
    medium = sorted({f"- {name} (ZAP rule {rule}, {report.removesuffix('.json')})" for report, rule, name, risk in failing if risk < 3})
    lines = ["The weekly ZAP active scan (scripts/zap_scan.py --active) found alerts of medium risk or worse that "
             "zap-rules.tsv doesn't waive.", ""]
    if medium:
        lines += ["Medium:", *medium, ""]
    if high:
        lines += [f"High: {len(high)}. Not listed here: run the scan locally for details, and handle them as a "
                  "vulnerability report (SECURITY.md).", ""]
    lines.append("Fix each, or waive it in zap-rules.tsv with a reason.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sign-in-url", default="http://localhost:8000", help="OpenClear, as this machine reaches it")
    parser.add_argument("--target", default=None, help="OpenClear, as ZAP's container reaches it (default: --sign-in-url)")
    parser.add_argument("--network", default=None, help="the Docker network ZAP joins")
    parser.add_argument("--password", default=os.environ.get("ZAP_PASSWORD"))
    parser.add_argument("--active", action="store_true")
    parser.add_argument("--minutes", default="3", help="how long the spider runs per user")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--summary", help="write an issue's text here if anything of medium risk or worse is unwaived")
    args = parser.parse_args()
    target = (args.target or args.sign_in_url).rstrip("/")
    WORK.mkdir(exist_ok=True)
    reports = []
    for label, email in USERS.items():
        cookie = sign_in(args.sign_in_url.rstrip("/"), email, args.password)
        report = f"{label}{'-active' if args.active else ''}.json"
        script = "zap-full-scan.py" if args.active else "zap-baseline.py"
        zap(args, script, report, ["-t", f"{target}/ui/", "-m", args.minutes, "-I"], ("Cookie", cookie))
        reports.append(report)
    if args.active:
        (WORK / "openapi.json").write_text((ROOT / "openapi" / "openclear-v1.json").read_text())
        token = api_token(args.sign_in_url.rstrip("/"), USERS["bank-admin"], args.password)
        zap(args, "zap-api-scan.py", "api-active.json", ["-t", "/zap/wrk/openapi.json", "-f", "openapi", "-O", target, "-I"],
            ("Authorization", f"Bearer {token}"))
        reports.append("api-active.json")

    accepted, failing = waived(), []
    for report in reports:
        print(f"\n{report}")
        for alert in sorted(alerts(WORK / report), key=lambda a: -int(a.get("riskcode", 0))):
            risk, rule = int(alert.get("riskcode", 0)), str(alert.get("pluginid"))
            reason = _waiver(accepted, alert)
            note = f"  waived: {reason}" if reason else ""
            name = "(not shown: see the report in .zap/)" if risk >= 3 and args.active and os.environ.get("CI") else alert.get("name")
            print(f"  {RISKS.get(risk, risk):13} {rule:>6}  {name} ({len(alert.get('instances', []))}){note}")
            if risk >= 2 and not reason:
                failing.append((report, rule, alert.get("name"), risk))
    if failing and args.summary:
        Path(args.summary).write_text(_summary(failing))
    if failing:
        print(f"\n{len(failing)} alert(s) of medium risk or worse that zap-rules.tsv doesn't waive.")
        return 0 if args.active else 1
    print("\nNo unwaived alerts of medium risk or worse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
