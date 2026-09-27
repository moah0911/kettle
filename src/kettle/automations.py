"""Automation matching — pure filter evaluation for provider events."""

from __future__ import annotations

from pydantic import BaseModel


class Automation(BaseModel):
    name: str
    on: str
    condition: str = ""
    action: str = "coordinator"


def match_automation(
    event_type: str, context: dict, automations: list[Automation]
) -> Automation | None:
    """Very small matcher for v0.1: exact `on` match + optional label/branch guards."""
    for auto in automations:
        if auto.on != event_type:
            continue
        cond = auto.condition
        if "label == 'factory'" in cond and "factory" not in context.get("labels", []):
            continue
        if "branch startswith 'factory/'" in cond and not str(context.get("branch", "")).startswith(
            "factory/"
        ):
            continue
        if (
            "mentions contains 'foreman'" in cond
            and "foreman" not in str(context.get("mentions", "")).lower()
        ):
            continue
        return auto
    return None
