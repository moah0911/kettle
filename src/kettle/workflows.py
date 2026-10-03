"""Temporal workflows — deterministic orchestration only. All I/O lives in activities."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from . import activities
    from .coordinator import handoff_allowed, route_after_story
    from .models import ReviewVerdict, Stage

# Versioned default: pass max_revisions as workflow arg to avoid replay breaks
# when the constant changes. Kept in sync with coordinator.MAX_REVISION_CYCLES.
DEFAULT_MAX_REVISIONS = 2

# Bumped whenever the *sequence* of activities or wait_conditions changes.
# Temporal replays in-flight workflows against new code; a reordered stage list
# is a non-determinism error, not a bug. Add a workflow.version() patch around
# the change when this moves.
#
# v3: open_handoff became conditional. A rejected or capped run no longer opens a
#     draft PR, which changes the recorded activity sequence on that path.
STAGE_SEQUENCE_VERSION = 3


@workflow.defn
class CoordinatorWorkflow:
    """One conversation per work item. Signals: approval, question_answer, cancel."""

    def __init__(self) -> None:
        self._approved = False
        self._approved_seen_spec = False
        self._story_approved = False
        self._story_seen = False
        self._cancelled = False
        self._answer: str | None = None
        self._question_id = 0

    @workflow.signal
    async def approve_story(self, story_version: int = 1) -> None:
        # Mirrors approve_spec: approvals arriving before the story stage ran
        # are ignored rather than silently satisfying a future gate.
        if self._story_seen:
            self._story_approved = True

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
        if stage == Stage.STORY.value:
            await workflow.execute_activity(
                activities.run_story,
                args=[work_item_id, title, body, repo],
                start_to_close_timeout=timedelta(minutes=10),
            )
            # Business-intent gate. A human decides whether this is the right
            # problem before any technical design happens.
            self._story_seen = True
            await workflow.wait_condition(lambda: self._story_approved or self._cancelled)
            if self._cancelled:
                return Stage.CANCELLED.value
            # A refused story parks for a human; there is no code to hand off.
            stage = route_after_story(self._story_approved).value
            if stage == Stage.HANDOFF.value:
                return stage
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
        # A string, matching the activity's return type. Initialize to a verdict
        # that blocks the handoff so a loop that never runs cannot reach the PR.
        final_verdict: str = ReviewVerdict.REQUEST_CHANGES.value
        while True:
            await workflow.execute_activity(
                activities.run_stage,
                args=[work_item_id, "implement", repo],
                start_to_close_timeout=timedelta(minutes=60),
            )
            final_verdict = await workflow.execute_activity(
                activities.run_review,
                args=[work_item_id, repo],
                start_to_close_timeout=timedelta(minutes=30),
            )
            if final_verdict == ReviewVerdict.APPROVE.value:
                break
            if final_verdict == ReviewVerdict.REJECT.value or revisions >= max_revisions:
                break
            revisions += 1
        if not handoff_allowed(ReviewVerdict(final_verdict)):
            # Rejected, or out of revision cycles. The branch exists and is pushed,
            # but no pull request is opened — see coordinator.handoff_allowed.
            return Stage.HANDOFF.value
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
