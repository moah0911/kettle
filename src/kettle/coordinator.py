"""Coordinator routing — pure functions so Temporal workflows stay deterministic.

Policy (mirrors the stage diagram):
intake -> triage -> plan? -> planning -> approval -> building -> reviewing -> revision? -> handoff.
"""

from __future__ import annotations

from .models import ReviewVerdict, Stage, WorkItem

MAX_REVISION_CYCLES = 2
TRIVIAL_LABELS = {"trivial", "docs", "chore", "small"}


def should_skip_triage(item: WorkItem) -> bool:
    """Skip triage when the request already explains problem + change."""
    body = (item.body or "").strip()
    if len(body) < 80:
        return False
    markers = ("repro", "stack trace", "expected", "acceptance", "files to change", "steps")
    hits = sum(1 for m in markers if m in body.lower())
    return hits >= 2 or "plan:" in body.lower()


def needs_planning(item: WorkItem, triage_complexity: str = "") -> bool:
    """Small, well-understood changes skip planning."""
    labels = {label.lower() for label in item.labels}
    if labels & TRIVIAL_LABELS:
        return False
    if triage_complexity.lower() in {"xs", "s", "trivial"}:
        return False
    return not (len(item.body or "") < 200 and not triage_complexity)


def route_after_triage(item: WorkItem, triage_complexity: str = "") -> Stage:
    if needs_planning(item, triage_complexity):
        return Stage.PLANNING
    return Stage.BUILDING


def route_after_review(verdict: ReviewVerdict, revision_count: int) -> Stage:
    if verdict == ReviewVerdict.APPROVE:
        return Stage.HANDOFF
    if verdict == ReviewVerdict.REJECT:
        return Stage.HANDOFF  # human decides; never auto-ship a rejection
    # request_changes
    if revision_count >= MAX_REVISION_CYCLES:
        return Stage.HANDOFF  # park on human instead of looping forever
    return Stage.BUILDING


def initial_stage(item: WorkItem) -> Stage:
    if should_skip_triage(item):
        return route_after_triage(item)
    return Stage.TRIAGE
