"""Factory-as-code loader + `kettle check` validation."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    model: str
    harness: str = "read-only"
    instructions: str = ""
    must_not: list[str] = Field(default_factory=list)
    output_format: str = ""
    skills: list[str] = Field(default_factory=list)

    def is_complete(self) -> bool:
        """An agent is only usable when purpose, boundaries, and contract exist."""
        return bool(self.instructions.strip() and self.must_not and self.output_format.strip())


class RunnerConfig(BaseModel):
    backend: str = "kubernetes"
    image: str = "ghcr.io/kettle/agent-runner:latest"
    resources: dict = Field(default_factory=dict)
    branch_prefix: str = "factory/"


class FactoryDefinition(BaseModel):
    factory_name: str = "default"
    repos: list[str] = Field(default_factory=list)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    automations: list[dict] = Field(default_factory=list)
    runners: dict[str, RunnerConfig] = Field(default_factory=dict)
    scorers: list[dict] = Field(default_factory=list)
    webhooks: list[dict] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


REQUIRED_AGENTS = {"coordinator", "triage", "story", "spec", "implement", "review"}

# Stages that write to the target repository. Their boundaries are load-bearing:
# a scope leak here lands in a pull request a human has to unpick.
WRITE_STAGES = {"story", "spec", "implement"}


def _forbids_merge(agent: AgentConfig) -> bool:
    """The no-merge invariant must be stated by the agent, not implied by policy."""
    return any("merge" in rule.lower() for rule in agent.must_not)


def _known_harnesses() -> set[str]:
    """Harness names the runtime can actually resolve. Imported lazily: the
    registry lives in harnesses, the schema lives here, and neither may import
    the other at module load."""
    from .harnesses import HARNESSES

    return set(HARNESSES)


def _normalize_automation(raw: dict) -> dict:
    """Accept YAML `if/do` spelling and normalize to condition/action.

    PyYAML parses unquoted `on:` as boolean True — map it back.
    """
    out = dict(raw)
    if True in out and "on" not in out:
        out["on"] = out.pop(True)
    if "if" in out and "condition" not in out:
        out["condition"] = out.pop("if")
    if "do" in out and "action" not in out:
        out["action"] = out.pop("do")
    return out


def load_factory(path: str | Path) -> FactoryDefinition:
    p = Path(path)
    if p.is_dir():
        p = p / "kettle.yaml"
    data = yaml.safe_load(p.read_text()) or {}
    agents = {k: AgentConfig(**v) for k, v in (data.get("agents") or {}).items()}
    runners = {k: RunnerConfig(**v) for k, v in (data.get("runners") or {}).items()}
    automations = [_normalize_automation(a) for a in (data.get("automations") or [])]
    return FactoryDefinition(
        factory_name=data.get("factory", {}).get("name", "default"),
        repos=data.get("factory", {}).get("repos", []),
        agents=agents,
        automations=automations,
        runners=runners,
        scorers=data.get("scorers", []),
        webhooks=data.get("webhooks", []),
        skills=data.get("skills", []),
    )


def check_factory(defn: FactoryDefinition) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_AGENTS - set(defn.agents)
    if missing:
        errors.append(f"missing agents: {sorted(missing)}")
    impl = defn.agents.get("implement")
    rev = defn.agents.get("review")
    if impl and rev and impl.model.split("/")[0] == rev.model.split("/")[0]:
        errors.append("review model vendor must differ from implement model vendor")
    if not defn.repos or any(not r or "/" not in r for r in defn.repos):
        errors.append("factory.repos must be non-empty owner/repo entries")
    for name, runner in defn.runners.items():
        resources = runner.resources or {}
        if not resources.get("cpu") or not resources.get("memory"):
            errors.append(f"runner {name}: set cpu+memory limits")
        timeout = resources.get("timeoutMinutes", 30)
        if not isinstance(timeout, int) or timeout <= 0 or timeout > 120:
            errors.append(f"runner {name}: timeoutMinutes must be int 1..120")
        if runner.backend not in {"kubernetes", "docker"}:
            errors.append(f"runner {name}: backend must be kubernetes|docker")
        if not runner.image:
            errors.append(f"runner {name}: image required")
        if not runner.branch_prefix:
            errors.append(f"runner {name}: branch_prefix required")
    scorer_names = {s.get("name", "") for s in defn.scorers} if defn.scorers else set()
    if "tests-pass" not in scorer_names:
        errors.append("no scorers defined (need at least tests-pass)")
    if not defn.automations:
        errors.append("no automations defined")
    for a in defn.automations:
        if not a.get("name") or not a.get("on"):
            errors.append(f"automation missing name/on: {a}")
    for agent_name, agent in defn.agents.items():
        if not agent.instructions:
            errors.append(f"agent {agent_name}: instructions required")
        if not agent.harness:
            errors.append(f"agent {agent_name}: harness required")
        elif agent.harness not in _known_harnesses():
            errors.append(f"agent {agent_name}: unknown harness {agent.harness!r}")
        if not agent.must_not:
            errors.append(f"agent {agent_name}: must_not boundaries required")
        if not agent.output_format:
            errors.append(f"agent {agent_name}: output_format contract required")
        if not agent.skills and not defn.skills:
            errors.append(f"agent {agent_name}: no skills (agent-level or factory-level)")
        if agent_name in WRITE_STAGES and not _forbids_merge(agent):
            errors.append(f"agent {agent_name}: must_not must explicitly forbid merging")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a factory definition")
    ap.add_argument("--factory", default="./factory")
    args = ap.parse_args()
    defn = load_factory(args.factory)
    errors = check_factory(defn)
    if errors:
        print("FAIL")
        for e in errors:
            print(f" - {e}")
        return 1
    print(f"OK: {defn.factory_name} agents={sorted(defn.agents)} repos={defn.repos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
