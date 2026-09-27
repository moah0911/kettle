"""Temporal worker entrypoint."""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import (
    check_dor,
    classify_triage,
    decide_after_triage,
    decide_initial_stage,
    open_handoff,
    record_run_result,
    run_review,
    run_stage,
    score_run,
)
from .workflows import CoordinatorWorkflow, StageWorkflow


async def main() -> None:
    host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    client = await Client.connect(host)
    worker = Worker(
        client,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "kettle"),
        workflows=[CoordinatorWorkflow, StageWorkflow],
        activities=[
            decide_initial_stage,
            decide_after_triage,
            classify_triage,
            check_dor,
            run_stage,
            run_review,
            open_handoff,
            record_run_result,
            score_run,
        ],
    )
    print(f"worker polling {host}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
