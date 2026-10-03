"""Knowledge layer — factory-as-code compiled into runtime system prompts.

Agent purpose, boundaries, and output contract live in `factory/kettle.yaml`.
This module is the only place that turns that declarative config into text a
model actually reads. `factory_check` can prove the config is well-formed; only
this module proves it is delivered.

Boundaries and output format are mandatory in the schema because they are the
two things agent definitions usually omit. Without boundaries an agent widens
scope; without an output contract the next agent in the chain cannot rely on
the shape it receives.
"""

from __future__ import annotations

import os

from .factory_check import AgentConfig, FactoryDefinition, load_factory

_STAGE_CONTEXT_HEADER = "## Stage context"
_BOUNDARIES_HEADER = "## Boundaries — never do these"
_OUTPUT_HEADER = "## Output format"
_SKILLS_HEADER = "## Skills"


def factory_dir() -> str:
    return os.getenv("FACTORY_DIR", "./factory")


def load_definition() -> FactoryDefinition:
    return load_factory(factory_dir())


def require_agent(stage: str, defn: FactoryDefinition | None = None) -> AgentConfig:
    """Fail closed: an unconfigured agent must never run on a guessed prompt."""
    definition = defn if defn is not None else load_definition()
    agent = definition.agents.get(stage)
    if agent is None:
        raise RuntimeError(f"factory defines no agent for stage {stage!r}")
    return agent


def skills_for(stage: str, defn: FactoryDefinition | None = None) -> list[str]:
    """Per-agent skills, falling back to the factory-wide list."""
    definition = defn if defn is not None else load_definition()
    agent = definition.agents.get(stage)
    if agent is not None and agent.skills:
        return list(agent.skills)
    return list(definition.skills)


def compose_system_prompt(
    agent: AgentConfig,
    *,
    stage: str = "",
    skills: list[str] | None = None,
    context: str = "",
) -> str:
    """Build the full system prompt: purpose, boundaries, output contract, skills."""
    sections = [agent.instructions.strip()]
    if context.strip():
        sections.append(f"{_STAGE_CONTEXT_HEADER}\n{context.strip()}")
    if agent.must_not:
        rules = "\n".join(f"- {rule}" for rule in agent.must_not)
        sections.append(f"{_BOUNDARIES_HEADER}\n{rules}")
    if agent.output_format.strip():
        sections.append(f"{_OUTPUT_HEADER}\n{agent.output_format.strip()}")
    if skills:
        listed = "\n".join(f"- {name}" for name in skills)
        sections.append(f"{_SKILLS_HEADER}\n{listed}")
    if stage:
        sections.append(f"You are the {stage} agent for this repository.")
    return "\n\n".join(section for section in sections if section)


def stage_system_prompt(
    stage: str,
    *,
    context: str = "",
    defn: FactoryDefinition | None = None,
) -> str:
    """Load the stage's agent config and compose its system prompt in one step."""
    definition = defn if defn is not None else load_definition()
    agent = require_agent(stage, definition)
    return compose_system_prompt(
        agent, stage=stage, skills=skills_for(stage, definition), context=context
    )
