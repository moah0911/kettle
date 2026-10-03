"""Harness adapters — same prompt, different executors.

Two kinds exist, and the distinction is load-bearing:

- Read-only harnesses never execute anything. They return the agent output as
  evidence. Triage, spec, story, and review reasoning all end here.
- Agent-CLI harnesses shell out to an installed CLI (`claude-code`, `codex`,
  `opencode`) — but only inside an explicit `workdir`. An executing harness
  called without one refuses to run. There is no default directory, because any
  default would be the worker's own checkout.

Nothing here ever passes model output to a bare shell. A prompt is data for an
agent CLI, not a command line.
"""

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
    """Safe default: read-only. Returns the prompt as evidence, executes nothing.

    New harness types inherit this behavior unless they explicitly opt into
    execution by subclassing AgentCliHarness. Forgetting to set a flag must
    never be what makes a harness execute.
    """

    name = "read-only"
    executes = False

    def run(
        self,
        *,
        work_item_id: str,
        stage: str,
        repo: str,
        branch: str,
        prompt: str,
        workdir: str | None = None,
    ) -> HarnessResult:
        return HarnessResult(branch=branch, tests_exit_code=0, evidence=prompt[:2000])


class AgentCliHarness(Harness):
    """Base for harnesses that invoke an agent CLI. Execution requires a workdir."""

    executes = True
    command: tuple[str, ...] = ()

    def run(
        self,
        *,
        work_item_id: str,
        stage: str,
        repo: str,
        branch: str,
        prompt: str,
        workdir: str | None = None,
    ) -> HarnessResult:
        if not workdir:
            raise RuntimeError(f"harness {self.name} refuses to run without a workdir")
        if not self.command:
            raise RuntimeError(f"harness {self.name} defines no command")
        try:
            proc = subprocess.run(
                list(self.command) + [prompt],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                cwd=workdir,
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


class ClaudeCodeHarness(AgentCliHarness):
    name = "claude-code"
    command = ("claude-code", "exec")


class CodexHarness(AgentCliHarness):
    name = "codex"
    command = ("codex", "exec")


class OpenCodeHarness(AgentCliHarness):
    name = "opencode"
    command = ("opencode", "run")


HARNESSES: dict[str, Harness] = {
    "read-only": Harness(),
    "claude-code": ClaudeCodeHarness(),
    "codex": CodexHarness(),
    "opencode": OpenCodeHarness(),
}


def get_harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        raise ValueError(f"unknown harness: {name}") from None
