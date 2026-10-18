# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The dependency gate (OWASP_PLAN.md OW-D8): runs pip-audit on the hashed lock files and fails
on a known vulnerability that has a fix, unless it's waived. A vulnerability with no fix yet
only warns. A waiver lives in .pip-audit-waivers.toml with a reason and an expiry date; an
expired waiver fails the run, so nothing is waived forever.

    python scripts/audit_dependencies.py [lock files...]   # default: requirements/*.txt
"""

import datetime as dt
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAIVERS = ROOT / ".pip-audit-waivers.toml"


def load_waivers(today: dt.date) -> tuple[dict[str, dict], list[str]]:
    """{vulnerability id: waiver}, and the problems with the file (expired, missing fields)."""
    if not WAIVERS.exists():
        return {}, []
    waivers, problems = {}, []
    for w in tomllib.loads(WAIVERS.read_text(encoding="utf-8")).get("waiver", []):
        vid, reason, expires = w.get("id"), w.get("reason"), w.get("expires")
        if not (vid and reason and isinstance(expires, dt.date)):
            problems.append(f"waiver {vid or '?'}: needs id, reason and expires (a date)")
        elif expires < today:
            problems.append(f"waiver {vid} expired on {expires}: fix it, or renew the waiver with a new reason")
        else:
            waivers[vid] = w
    return waivers, problems


def audit(lock: Path) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-r", str(lock), "--require-hashes", "--disable-pip",
         "--progress-spinner", "off", "--format", "json"],
        capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):  # 1 means vulnerabilities were found
        sys.exit(f"pip-audit failed on {lock}:\n{result.stderr}")
    report = json.loads(result.stdout)
    return [
        {"package": dep["name"], "version": dep["version"], **vuln}
        for dep in report.get("dependencies", [])
        for vuln in dep.get("vulns", [])
    ]


def main(argv: list[str]) -> int:
    locks = [Path(a) for a in argv] or sorted((ROOT / "requirements").glob("*.txt"))
    waivers, problems = load_waivers(dt.date.today())
    failing = []
    for lock in locks:
        for v in audit(lock):
            ids = {v["id"], *v.get("aliases", [])}
            label = f"{lock.name}: {v['package']} {v['version']} {v['id']}"
            if ids & waivers.keys():
                print(f"waived   {label} ({waivers[next(iter(ids & waivers.keys()))]['reason']})")
            elif v.get("fix_versions"):
                failing.append(f"{label}: fixed in {', '.join(v['fix_versions'])}")
            else:
                print(f"warning  {label}: no fixed version yet")
    failing = list(dict.fromkeys(failing))  # pip-audit can report one vulnerability twice
    for line in problems + failing:
        print(f"FAIL     {line}")
    if not (problems or failing):
        print(f"ok: {len(locks)} lock file(s), no unwaived vulnerability with a fix")
    return 1 if problems or failing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
