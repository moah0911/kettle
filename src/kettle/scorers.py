"""Scorers — tiny pure predicates that define the scoring vocabulary.

`POST /v1/scores` accepts results under the `tests-pass` / `criteria-met`
names (the factory definition is required to declare at least `tests-pass`);
these constructors define what those names mean, and `group_failures` turns a
batch of them into the single label the dashboard reports.
"""

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
