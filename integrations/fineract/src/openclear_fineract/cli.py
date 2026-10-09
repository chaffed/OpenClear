# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""`openclear-fineract run` (or sync, presented, decisions), for cron. Configuration from the
environment: OPENCLEAR_URL/CLIENT_ID/CLIENT_SECRET, FINERACT_URL/USERNAME/PASSWORD/TENANT,
and OPENCLEAR_FINERACT_STATE (the state file, default ./openclear-fineract.sqlite)."""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

from openclear_sdk import OpenClear

from openclear_fineract.adapter import Adapter, State
from openclear_fineract.fineract import Fineract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openclear-fineract")
    parser.add_argument("step", choices=["run", "sync", "presented", "decisions"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    env = os.environ
    adapter = Adapter(
        OpenClear(env["OPENCLEAR_URL"], client_id=env["OPENCLEAR_CLIENT_ID"], client_secret=env["OPENCLEAR_CLIENT_SECRET"]),
        Fineract(env["FINERACT_URL"], username=env["FINERACT_USERNAME"], password=env["FINERACT_PASSWORD"],
                 tenant=env.get("FINERACT_TENANT", "default")),
        State(env.get("OPENCLEAR_FINERACT_STATE", "openclear-fineract.sqlite")),
    )
    report = getattr(adapter, args.step)()
    print(json.dumps(asdict(report), indent=2))
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
