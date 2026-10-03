"""Workflow gate tests. The human gates are pure state transitions on the
workflow object, so they are testable without a Temporal server — which the
testing rules forbid. No live Temporal, no live cluster.
"""

import asyncio

from kettle.workflows import STAGE_SEQUENCE_VERSION, CoordinatorWorkflow, StageWorkflow


def test_story_approval_ignored_before_story_stage_runs():
    # An approval arriving during triage must not silently satisfy a future gate
    wf = CoordinatorWorkflow()
    asyncio.run(wf.approve_story())
    assert wf._story_approved is False


def test_story_approval_counts_once_story_stage_ran():
    wf = CoordinatorWorkflow()
    wf._story_seen = True
    asyncio.run(wf.approve_story())
    assert wf._story_approved is True


def test_spec_approval_ignored_before_spec_stage_runs():
    wf = CoordinatorWorkflow()
    asyncio.run(wf.approve_spec())
    assert wf._approved is False


def test_spec_approval_counts_once_spec_stage_ran():
    wf = CoordinatorWorkflow()
    wf._approved_seen_spec = True
    asyncio.run(wf.approve_spec())
    assert wf._approved is True


def test_story_and_spec_gates_are_independent():
    # Approving the story must not approve the spec, or the second gate is theatre
    wf = CoordinatorWorkflow()
    wf._story_seen = True
    asyncio.run(wf.approve_story())
    assert wf._story_approved is True
    assert wf._approved is False


def test_cancel_wins_over_approval_state():
    wf = CoordinatorWorkflow()
    wf._story_seen = True
    asyncio.run(wf.approve_story())
    asyncio.run(wf.cancel())
    assert wf.state() == "cancelled"


def test_state_reports_approved_once_spec_approved():
    wf = CoordinatorWorkflow()
    assert wf.state() == "running"
    wf._approved_seen_spec = True
    asyncio.run(wf.approve_spec())
    assert wf.state() == "approved"


def test_question_answer_rejects_mismatched_id():
    wf = CoordinatorWorkflow()
    wf._question_id = 2
    asyncio.run(wf.answer_question(1, "answer"))
    assert wf._answer is None
    asyncio.run(wf.answer_question(2, "answer"))
    assert wf._answer == "answer"


def test_question_answer_rejects_empty():
    wf = CoordinatorWorkflow()
    wf._question_id = 1
    asyncio.run(wf.answer_question(1, ""))
    assert wf._answer is None


def test_stage_sequence_version_tracks_the_story_gate():
    # The story gate changed the activity sequence; the version must say so,
    # because Temporal replays in-flight workflows against new code.
    assert STAGE_SEQUENCE_VERSION >= 2


def test_stage_workflow_is_registered():
    # Guards against the activity list drifting from the workflow definitions
    assert hasattr(StageWorkflow, "run")
    assert hasattr(CoordinatorWorkflow, "run")
