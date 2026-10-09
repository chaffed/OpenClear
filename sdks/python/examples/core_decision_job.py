# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""A core's decision job: run it every few minutes (cron, a scheduler). It pays or returns
each newly decided item and acknowledges what it handled, so OpenClear shows the core as
current and the next run starts where this one stopped.

    OPENCLEAR_URL=... OPENCLEAR_CLIENT_ID=... OPENCLEAR_CLIENT_SECRET=... python core_decision_job.py
"""

import os

from openclear_sdk import OpenClear


def handle(decision: dict) -> None:
    item = decision.get("check_number") or decision.get("trace_number")
    if decision["outcome"] == "return":
        # Your core's return call, with the reason OpenClear recorded:
        print(f"RETURN {decision['account_number']} {item} {decision['amount']} code={decision.get('return_transaction_code')}")
    else:
        print(f"PAY    {decision['account_number']} {item} {decision['amount']}")


if __name__ == "__main__":
    with OpenClear(os.environ["OPENCLEAR_URL"], client_id=os.environ["OPENCLEAR_CLIENT_ID"],
                   client_secret=os.environ["OPENCLEAR_CLIENT_SECRET"]) as oc:
        last = oc.decision_feed.consume("core", handle)
        print(f"Handled through decision {last}")
