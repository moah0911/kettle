"""Harness adapters — same prompt, different executors.

Real CLI harnesses execute only when KETTLE_LIVE_HARNESS=1; otherwise run()
returns an explicit dry-run result so tests/dev never mistake it for real work.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass
class HarnessResult:
    branch: str
    commit: str = ""
    tests_exit_code: int = 0
    evidence: str = ""
    dry_run: bool = True


def is_live_harness() -> bool:
    return os.getenv("KETTLE_LIVE_HARNESS") == "1"


class Harness:
    name = "shell"
    command: tuple[str, ...] = ()

    def run(
        self, *, work_item_id: str, stage: str, repo: str, branch: str, prompt: str
    ) -> HarnessResult:
        if not is_live_harness() or not self.command:
            return HarnessResult(
                branch=branch,
                commit="",
                tests_exit_code=0,
                evidence=f"[dry-run:{self.name}:{stage}] {work_item_id} in {repo}",
                dry_run=True,
            )
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
                dry_run=False,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as failed result
            return HarnessResult(
                branch=branch,
                commit="",
                tests_exit_code=1,
                evidence=f"harness {self.name} failed: {exc}",
                dry_run=False,
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
    return HARNESSES.get(name, ShellHarness())
