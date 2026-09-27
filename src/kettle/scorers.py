"""Scorers — classify completed runs; self-improvement groups failures into new work items."""

from __future__ import annotations

from pydantic import BaseModel


class Score(BaseModel):
    scorer: str
    passed: bool
    reason: str


def score_tests_pass(test_exit_code: int) -> Score:
    return Score(
        scorer="tests-pass",
        passed=test_exit_code == 0,
        reason="exit 0" if test_exit_code == 0 else f"exit {test_exit_code}",
    )


def score_criteria_met(met: int, total: int, evidence_refs: int = 0) -> Score:
    if total <= 0:
        return Score(scorer="criteria-met", passed=False, reason="no criteria defined")
    if met > total:
        return Score(
            scorer="criteria-met", passed=False, reason=f"{met}/{total} invalid: met > total"
        )
    if met == total and evidence_refs < total:
        return Score(
            scorer="criteria-met", passed=False, reason=f"{met}/{total} missing file:line evidence"
        )
    return Score(
        scorer="criteria-met",
        passed=met == total,
        reason=f"{met}/{total} criteria with evidence",
    )


def group_failures(scores: list[Score]) -> str:
    by_name = {s.scorer: s for s in scores}
    review = by_name.get("review")
    if review is not None and not review.passed:
        return "review-reject"
    failed = [s.scorer for s in scores if not s.passed]
    if not failed:
        return "none"
    if "tests-pass" in failed:
        return "test-failures"
    return "criteria-gaps"


class BenchmarkResult(BaseModel):
    config: str
    passed: int
    total: int
    cost_usd: float = 0.0


def compare_benchmarks(results: list[BenchmarkResult]) -> BenchmarkResult | None:
    """Pick best pass-rate, break ties on cost (snowl-style cost-aware scoring)."""
    measured = [r for r in results if r.total > 0]
    if not measured:
        return None
    return min(measured, key=lambda r: (-(r.passed / r.total), r.cost_usd))


class FrontierEntry(BaseModel):
    """Top-N programs (prompt+skills) tracked as versioned configs (EvoSkill pattern)."""

    name: str
    score: float
    config: dict = {}


def update_frontier(
    frontier: list[FrontierEntry], candidate: FrontierEntry, size: int = 3
) -> list[FrontierEntry]:
    if size <= 0:
        raise ValueError("frontier size must be >= 1")
    deduped = [e for e in frontier if e.name != candidate.name]
    merged = sorted([*deduped, candidate], key=lambda e: -e.score)[:size]
    return merged


def propose_followup(group: str, work_item_id: str, repo: str) -> dict | None:
    """Self-improvement: group failures into a follow-up work item for human review."""
    if group == "none":
        return None
    title = f"Fix {group} from {work_item_id}"
    body = f"Automated follow-up for {work_item_id} (group={group}). Propose fix as draft PR; never auto-merge."
    return {"title": title, "body": body, "repo": repo, "labels": ["factory", "followup"]}
