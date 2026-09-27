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


def score_criteria_met(met: int, total: int) -> Score:
    return Score(
        scorer="criteria-met",
        passed=total > 0 and met == total,
        reason=f"{met}/{total} criteria with evidence",
    )


def group_failures(scores: list[Score]) -> str:
    failed = [s.scorer for s in scores if not s.passed]
    if not failed:
        return "none"
    if "tests-pass" in failed:
        return "test-failures"
    return "criteria-gaps"
