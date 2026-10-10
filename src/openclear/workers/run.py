# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""`openclear-worker`: runs background jobs (workers/jobs.py) in its own process.

Use it with OPENCLEAR_WORKER_MODE=external on the web processes, so they only queue work,
and run as many of these as the load needs (each claims jobs independently; Postgres
SKIP LOCKED keeps them out of each other's way). Stops cleanly on SIGINT/SIGTERM after
finishing the job in hand. `--once` runs whatever is due and exits (for cron).
"""

import argparse
import logging
import signal
import threading

from openclear.db.session import get_engine
from openclear.workers.jobs import drain, run_forever


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run every job that's due, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from openclear import logging_config

    logging_config.configure()  # JSON lines when OPENCLEAR_LOG_FORMAT=json (SIEM_PLAN.md S1)

    import openclear.domain  # noqa: F401 -- registers every mapper
    from openclear import plugins

    plugins.load()  # the same networks, providers and formats as the web process

    engine = get_engine()
    if args.once:
        logging.getLogger(__name__).info("Ran %d job(s)", drain(engine))
        return

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run_forever(engine, stop)


if __name__ == "__main__":
    main()
