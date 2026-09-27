"""Coordinator routing — pure functions so Temporal workflows stay deterministic.

Policy (mirrors the stage diagram):
intake -> triage -> plan? -> planning -> approval -> building -> reviewing -> revision? -> handoff.
"""

from __future__ import annotations

from .models import DefinitionOfReady, ReviewVerdict, Stage, TriageVerdict, WorkItem

MAX_REVISION_CYCLES = 2
TRIVIAL_LABELS = {"trivial", "docs", "chore", "small"}


def classify(item: WorkItem) -> TriageVerdict:
    """Ported classifier station: fast, deterministic triage verdict.

    Real LLM classifier lands in activities; this pure version keeps
    workflows deterministic and testable.
    """
    labels = {label.lower() for label in item.labels}
    body = (item.body or "").lower()
    if not item.title and not body:
        return TriageVerdict(
            type="invalid", priority="p3", complexity="xs", actionable=False, reason="empty request"
        )
    if any(w in body for w in ("?", "how do i", "how to")) and len(body) < 200:
        return TriageVerdict(
            type="question",
            priority="p3",
            complexity="xs",
            actionable=False,
            reason="question, needs answer",
        )
    complexity = "m"
    if labels & TRIVIAL_LABELS or len(body) < 200:
        complexity = "s"
    if len(body) > 2000 or "migration" in body or "refactor" in body:
        complexity = "l"
    itype = "bug" if ("bug" in labels or "error" in body or "traceback" in body) else "feature"
    if labels & TRIVIAL_LABELS:
        itype = "chore"
    return TriageVerdict(
        type=itype,
        priority="p2",
        complexity=complexity,
        actionable=True,
        reason="heuristic classification",
    )


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
    """Cross-harness rule: review harness must differ from implement harness."""
    if stage == "review":
        impl_vendor = implement_model.split("/")[0] if "/" in implement_model else ""
        if impl_vendor.startswith("anthropic"):
            return "codex"
        return "claude-code"
    if stage == "implement":
        return "claude-code"
    return "shell"


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
