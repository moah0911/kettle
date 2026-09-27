"""Harness adapters — same prompt, different executors.

v0.1 executes via the LLM provider + git branch conventions. Real CLI
harnesses (claude-code/codex/opencode) plug in here in P3 without touching
workflows: implement `run()` for the new harness and register it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HarnessResult:
    branch: str
    commit: str = ""
    tests_exit_code: int = 0
    evidence: str = ""


class Harness:
    name = "shell"

    def run(
        self, *, work_item_id: str, stage: str, repo: str, branch: str, prompt: str
    ) -> HarnessResult:
        _ = prompt
        return HarnessResult(
            branch=branch,
            commit="",
            tests_exit_code=0,
            evidence=f"[{self.name}:{stage}] {work_item_id} in {repo}",
        )


class ShellHarness(Harness):
    name = "shell"


class ClaudeCodeHarness(Harness):
    name = "claude-code"


class CodexHarness(Harness):
    name = "codex"


class OpenCodeHarness(Harness):
    name = "opencode"


HARNESSES: dict[str, Harness] = {
    "shell": ShellHarness(),
    "claude-code": ClaudeCodeHarness(),
    "codex": CodexHarness(),
    "opencode": OpenCodeHarness(),
}


def get_harness(name: str) -> Harness:
    return HARNESSES.get(name, ShellHarness())
