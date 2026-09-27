"""Temporal workflows — deterministic orchestration only. All I/O lives in activities."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from . import activities
    from .models import ReviewVerdict, Stage

# Versioned default: pass max_revisions as workflow arg to avoid replay breaks
# when the constant changes. Kept in sync with coordinator.MAX_REVISION_CYCLES.
DEFAULT_MAX_REVISIONS = 2


@workflow.defn
class CoordinatorWorkflow:
    """One conversation per work item. Signals: approval, question_answer, cancel."""

    def __init__(self) -> None:
        self._approved = False
        self._approved_seen_spec = False
        self._cancelled = False
        self._answer: str | None = None
        self._question_id = 0

    @workflow.signal
    async def approve_spec(self, spec_version: int = 1) -> None:
        # Only approvals issued after the spec stage ran count; early signals
        # during triage are ignored via the _approved_seen_spec gate below.
        if self._approved_seen_spec:
            self._approved = True

    @workflow.signal
    async def answer_question(self, question_id: int, answer: str) -> None:
        if not answer or question_id != self._question_id:
            return
        self._answer = answer

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def state(self) -> str:
        return "cancelled" if self._cancelled else ("approved" if self._approved else "running")

    @workflow.run
    async def run(
        self,
        work_item_id: str,
        title: str,
        body: str,
        repo: str,
        labels: list[str],
        max_revisions: int = DEFAULT_MAX_REVISIONS,
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
            triage = await workflow.execute_activity(
                activities.classify_triage,
                args=[title, body, labels],
                start_to_close_timeout=timedelta(minutes=1),
            )
            dor = await workflow.execute_activity(
                activities.check_dor,
                args=[title, body, labels, triage],
                start_to_close_timeout=timedelta(minutes=1),
            )
            if not dor["ready"]:
                # Park on human: question gate instead of guessing.
                self._question_id += 1
                self._answer = None
                await workflow.wait_condition(lambda: self._answer is not None or self._cancelled)
                if self._cancelled:
                    return Stage.CANCELLED.value
                body = f"{body}\n\nAnswers: {self._answer}"
                self._answer = None
            stage = await workflow.execute_activity(
                activities.decide_after_triage,
                args=[title, body, labels, triage.get("complexity", "m")],
                start_to_close_timeout=timedelta(minutes=1),
            )
        if stage == Stage.PLANNING.value:
            await workflow.execute_activity(
                activities.run_stage,
                args=[work_item_id, "spec", repo],
                start_to_close_timeout=timedelta(minutes=30),
            )
            # Human approval gate — only signals after this point count.
            self._approved_seen_spec = True
            await workflow.wait_condition(lambda: self._approved or self._cancelled)
            if self._cancelled:
                return Stage.CANCELLED.value
            stage = Stage.BUILDING.value
        revisions = 0
        parked_on_human = False
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
            if verdict == ReviewVerdict.APPROVE.value:
                break
            if verdict == ReviewVerdict.REJECT.value or revisions >= max_revisions:
                parked_on_human = True
                break
            revisions += 1
        await workflow.execute_activity(
            activities.open_handoff,
            args=[work_item_id, repo],
            start_to_close_timeout=timedelta(minutes=5),
        )
        return Stage.HANDOFF.value if parked_on_human else Stage.COMPLETE.value


@workflow.defn
class StageWorkflow:
    @workflow.run
    async def run(self, work_item_id: str, stage: str, repo: str) -> str:
        return await workflow.execute_activity(
            activities.run_stage,
            args=[work_item_id, stage, repo],
            start_to_close_timeout=timedelta(minutes=60),
        )
