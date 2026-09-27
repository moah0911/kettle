"""Temporal workflows — deterministic orchestration only. All I/O lives in activities."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from . import activities
    from .coordinator import MAX_REVISION_CYCLES
    from .models import ReviewVerdict, Stage


@workflow.defn
class CoordinatorWorkflow:
    """One conversation per work item. Signals: approval, question_answer, cancel."""

    def __init__(self) -> None:
        self._approved = False
        self._cancelled = False
        self._answer: str | None = None

    @workflow.signal
    async def approve_spec(self) -> None:
        self._approved = True

    @workflow.signal
    async def answer_question(self, answer: str) -> None:
        self._answer = answer

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def state(self) -> str:
        return "cancelled" if self._cancelled else ("approved" if self._approved else "running")

    @workflow.run
    async def run(
        self, work_item_id: str, title: str, body: str, repo: str, labels: list[str]
    ) -> str:
        if self._cancelled:
            return Stage.CANCELLED.value
        stage = await workflow.execute_activity(
            activities.decide_initial_stage,
            args=[title, body, labels],
            start_to_close_timeout=timedelta(minutes=1),
        )
        if stage == Stage.TRIAGE.value:
            await workflow.execute_activity(
                activities.run_stage,
                args=[work_item_id, "triage", repo],
                start_to_close_timeout=timedelta(minutes=30),
            )
            if self._cancelled:
                return Stage.CANCELLED.value
            stage = await workflow.execute_activity(
                activities.decide_after_triage,
                args=[title, body, labels, "m"],
                start_to_close_timeout=timedelta(minutes=1),
            )
        if stage == Stage.PLANNING.value:
            await workflow.execute_activity(
                activities.run_stage,
                args=[work_item_id, "spec", repo],
                start_to_close_timeout=timedelta(minutes=30),
            )
            # Human approval gate — workflow waits here via signal.
            await workflow.wait_condition(lambda: self._approved or self._cancelled)
            if self._cancelled:
                return Stage.CANCELLED.value
            stage = Stage.BUILDING.value
        revisions = 0
        while True:
            await workflow.execute_activity(
                activities.run_stage,
                args=[work_item_id, "implement", repo],
                start_to_close_timeout=timedelta(minutes=60),
            )
            verdict = await workflow.execute_activity(
                activities.run_review,
                args=[work_item_id, repo],
                start_to_close_timeout=timedelta(minutes=30),
            )
            if verdict == ReviewVerdict.APPROVE.value or revisions >= MAX_REVISION_CYCLES:
                break
            if verdict == ReviewVerdict.REJECT.value:
                break
            revisions += 1
        await workflow.execute_activity(
            activities.open_handoff,
            args=[work_item_id, repo],
            start_to_close_timeout=timedelta(minutes=5),
        )
        return Stage.COMPLETE.value


@workflow.defn
class StageWorkflow:
    @workflow.run
    async def run(self, work_item_id: str, stage: str, repo: str) -> str:
        return await workflow.execute_activity(
            activities.run_stage,
            args=[work_item_id, stage, repo],
            start_to_close_timeout=timedelta(minutes=60),
        )
