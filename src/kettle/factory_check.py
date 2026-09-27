"""Factory-as-code loader + `kettle check` validation."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    model: str
    harness: str = "shell"
    instructions: str = ""


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


REQUIRED_AGENTS = {"coordinator", "triage", "spec", "implement", "review"}


def load_factory(path: str | Path) -> FactoryDefinition:
    p = Path(path)
    if p.is_dir():
        p = p / "kettle.yaml"
    data = yaml.safe_load(p.read_text()) or {}
    agents = {k: AgentConfig(**v) for k, v in (data.get("agents") or {}).items()}
    runners = {k: RunnerConfig(**v) for k, v in (data.get("runners") or {}).items()}
    return FactoryDefinition(
        factory_name=data.get("factory", {}).get("name", "default"),
        repos=data.get("factory", {}).get("repos", []),
        agents=agents,
        automations=data.get("automations", []),
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
    if not defn.repos:
        errors.append("factory.repos is empty")
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
