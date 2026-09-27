"""Trust model — stamped at intake, enforced at delivery.

Unattended runs (GitHub `factory` label, CI fix loops) get draft-PR/comment/
label/close only. No shared-config writes, no merge. Attended callers
(Slack/Linear/Jira with workspace membership) may write directly.
"""

from __future__ import annotations

TRUSTED_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}

# Tools unattended runs may call. Merge is absent everywhere on purpose.
UNATTENDED_ALLOW = {"open_draft_pr", "comment", "label", "close", "reopen", "read"}


def is_trusted_role(role: str) -> bool:
    return role in TRUSTED_ROLES


def allowed_tool(tool: str, trusted: bool, unattended: bool) -> bool:
    if tool == "merge":
        return False
    return not (unattended and tool not in UNATTENDED_ALLOW)
