"""Coordinator routing — pure functions so Temporal workflows stay deterministic.

Policy (mirrors the stage diagram):
intake -> triage -> story -> planning -> approval -> building -> reviewing
-> revision? -> handoff.

The story gate exists because business intent is a human judgement. Technical
design can be reviewed by another model; "is this the right problem?" cannot.
"""

from __future__ import annotations

from .models import DefinitionOfReady, ReviewVerdict, Stage, TriageVerdict, WorkItem

MAX_REVISION_CYCLES = 2
TRIVIAL_LABELS = {"trivial", "docs", "chore", "small"}


def check_definition_of_ready(item: WorkItem, verdict: TriageVerdict) -> DefinitionOfReady:
    """DoR gate (ai-sdlc pattern): no dispatch until acceptance + scope + questions resolve."""
    missing: list[str] = []
    if not verdict.actionable:
        missing.append("actionable triage verdict")
    body = item.body or ""
    if len(body) < 80:
        missing.append("acceptance criteria")
    if "?" in body and len(body) < 300:
        missing.append("open questions answered")
    if verdict.complexity in {"l", "xl"} and "plan:" not in body.lower():
        missing.append("bounded scope / plan")
    return DefinitionOfReady(ready=not missing, missing=missing)


def select_harness(stage: str, implement_model: str = "", review_model: str = "") -> str:
    """Cross-harness rule: review harness must differ from implement harness.

    Read-only stages never execute, so they get the harness that does nothing.
    """
    if stage == "review":
        impl_vendor = implement_model.split("/")[0] if "/" in implement_model else ""
        if impl_vendor.startswith("anthropic"):
            return "codex"
        return "claude-code"
    if stage == "implement":
        return "claude-code"
    return "read-only"


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
    """Anything large enough to plan gets a story first; trivial work skips both."""
    if needs_planning(item, triage_complexity):
        return Stage.STORY
    return Stage.BUILDING


def route_after_story(story_approved: bool) -> Stage:
    """An approved story proceeds to planning; a refused one stops at handoff."""
    return Stage.PLANNING if story_approved else Stage.HANDOFF


def route_after_review(verdict: ReviewVerdict, revision_count: int) -> Stage:
    if verdict == ReviewVerdict.APPROVE:
        return Stage.HANDOFF
    if verdict == ReviewVerdict.REJECT:
        return Stage.HANDOFF  # human decides; never auto-ship a rejection
    # request_changes
    if revision_count >= MAX_REVISION_CYCLES:
        return Stage.HANDOFF  # park on human instead of looping forever
    return Stage.BUILDING


def handoff_allowed(verdict: ReviewVerdict) -> bool:
    """Only an approved review earns a draft PR.

    A rejected or capped run still has a pushed branch, but no PR: the reviewer
    reads the report and decides whether it is worth proposing at all. Opening a
    PR that looks like ordinary work is how a rejection gets merged unread.
    """
    return verdict == ReviewVerdict.APPROVE


def initial_stage(item: WorkItem) -> Stage:
    if should_skip_triage(item):
        return route_after_triage(item)
    return Stage.TRIAGE
