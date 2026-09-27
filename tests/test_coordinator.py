"""Coordinator routing tests (AAA pattern, no live services)."""

from kettle.coordinator import (
    initial_stage,
    needs_planning,
    route_after_review,
    route_after_triage,
    should_skip_triage,
)
from kettle.models import ReviewVerdict, Stage, WorkItem


def _item(**kw) -> WorkItem:
    base = {"id": "wi-1", "source": "github", "title": "Fix crash", "body": "", "repo": "acme/app"}
    base.update(kw)
    return WorkItem(**base)


def test_should_skip_triage_with_rich_context():
    # Arrange
    item = _item(
        body="Repro steps: open login, submit empty form. Stack trace attached. "
        "Expected behavior: redirect to /home. Acceptance criteria: covered. "
        "Files to change: auth.py. " + "context " * 10
    )
    # Act
    result = should_skip_triage(item)
    # Assert
    assert result is True


def test_should_not_skip_triage_on_short_body():
    assert should_skip_triage(_item(body="broken")) is False


def test_needs_planning_skips_trivial_labels():
    assert needs_planning(_item(labels=["trivial"], body="x" * 500)) is False
    assert needs_planning(_item(labels=["bug"], body="x" * 500)) is True


def test_route_after_triage_respects_complexity():
    assert route_after_triage(_item(labels=["docs"])) == Stage.BUILDING
    assert route_after_triage(_item(labels=["bug"], body="x" * 500), "m") == Stage.PLANNING


def test_route_after_review_caps_revisions():
    assert route_after_review(ReviewVerdict.APPROVE, 0) == Stage.HANDOFF
    assert route_after_review(ReviewVerdict.REQUEST_CHANGES, 0) == Stage.BUILDING
    assert route_after_review(ReviewVerdict.REQUEST_CHANGES, 2) == Stage.HANDOFF
    assert route_after_review(ReviewVerdict.REJECT, 0) == Stage.HANDOFF


def test_initial_stage_short_circuits_when_rich():
    item = _item(
        labels=["bug"], body="Repro ... expected ... acceptance ... files to change ..." + "x" * 100
    )
    assert initial_stage(item) in {Stage.PLANNING, Stage.BUILDING}
