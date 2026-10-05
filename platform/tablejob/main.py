"""Run a table worker: python -m tablejob.main

One activity at a time. A table write takes a few hundred megabytes whatever the size of the file (see the measurements in
platform/api/app/config.py), so a container of a gigabyte is one job's worth, and more work is more containers, not more jobs
in one.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from . import config
from .activity import report_table_job_failure, write_table_job
from .workflow import TableWriteWorkflow

log = logging.getLogger("tablejob")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = config.load()
    client = await Client.connect(cfg.temporal)
    worker = Worker(client, task_queue=cfg.queue, workflows=[TableWriteWorkflow], activities=[write_table_job, report_table_job_failure],
                    max_concurrent_activities=1)
    log.info("table worker ready on %r%s", cfg.queue, f" for organisation {cfg.tenant!r} only" if cfg.tenant else " (the shared pool)")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
