"""Factory MCP server — lets local coding agents submit tasks and check status."""

from __future__ import annotations

# Tools: submit_task(title, body, repo) -> work_item_id; get_status(work_item_id) -> status.
# v0.1 exposes pure helpers; stdio transport wiring lands with the `mcp` package server.
from .models import WorkItem


def submit_task(title: str, body: str, repo: str) -> WorkItem:
    import uuid

    if not repo or "/" not in repo:
        raise ValueError("repo must be owner/repo or a https URL")
    return WorkItem(
        id=f"wi-{uuid.uuid4().hex[:8]}", source="mcp", title=title, body=body, repo=repo
    )


def describe_tools() -> list[dict]:
    return [
        {"name": "submit_task", "description": "Submit a task to the factory coordinator"},
        {"name": "get_status", "description": "Get work-item status by id"},
        {
            "name": "answer_question",
            "description": "Answer a coordinator question (unblocks waiting workflow)",
        },
    ]
