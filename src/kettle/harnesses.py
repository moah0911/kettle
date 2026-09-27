"""Harness adapters — same prompt, different executors. Always executes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class HarnessResult:
    branch: str
    commit: str = ""
    tests_exit_code: int = 0
    evidence: str = ""


class Harness:
    name = "shell"
    command: tuple[str, ...] = ("sh", "-c")

    def run(
        self, *, work_item_id: str, stage: str, repo: str, branch: str, prompt: str
    ) -> HarnessResult:
        try:
            proc = subprocess.run(
                list(self.command) + [prompt],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            return HarnessResult(
                branch=branch,
                commit="",
                tests_exit_code=proc.returncode,
                evidence=proc.stdout[:2000] or proc.stderr[:2000],
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as failed result
            return HarnessResult(
                branch=branch,
                commit="",
                tests_exit_code=1,
                evidence=f"harness {self.name} failed: {exc}",
            )


class ShellHarness(Harness):
    name = "shell"


class ClaudeCodeHarness(Harness):
    name = "claude-code"
    command = ("claude-code", "exec")


class CodexHarness(Harness):
    name = "codex"
    command = ("codex", "exec")


class OpenCodeHarness(Harness):
    name = "opencode"
    command = ("opencode", "run")


HARNESSES: dict[str, Harness] = {
    "shell": ShellHarness(),
    "claude-code": ClaudeCodeHarness(),
    "codex": CodexHarness(),
    "opencode": OpenCodeHarness(),
}


def get_harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        raise ValueError(f"unknown harness: {name}") from None
