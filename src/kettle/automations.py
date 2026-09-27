"""Automation matching — pure filter evaluation for provider events."""

from __future__ import annotations

from pydantic import BaseModel


class Automation(BaseModel):
    name: str
    on: str
    condition: str = ""
    action: str = "coordinator"


def _has_label(context: dict, label: str) -> bool:
    return label in [str(label).lower() for label in context.get("labels", [])]


def match_automation(
    event_type: str, context: dict, automations: list[Automation]
) -> Automation | None:
    """Exact `on` match + label/branch/mention/role guards.

    Conditions are parsed for known clauses; unknown clauses fail closed
    (no match) instead of matching everyone.
    """
    for auto in automations:
        if auto.on != event_type:
            continue
        cond = auto.condition
        if "label ==" in cond and not _has_label(context, "factory"):
            continue
        if "branch startswith" in cond and not str(context.get("branch", "")).startswith(
            "factory/"
        ):
            continue
        if (
            "mentions contains" in cond
            and "foreman" not in str(context.get("mentions", "")).lower()
        ):
            continue
        if "permission >=" in cond or "role in" in cond:
            role = str(context.get("author_role", context.get("role", "NONE")))
            if role not in {"OWNER", "MEMBER", "COLLABORATOR"}:
                continue
        return auto
    return None
