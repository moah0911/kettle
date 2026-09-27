"""Factory MCP server — lets local coding agents submit tasks and check status."""

from __future__ import annotations

# Tools: submit_task(title, body, repo) -> work_item_id; get_status(work_item_id) -> status.
# v0.1 exposes pure helpers; stdio transport wiring lands with the `mcp` package server.
from .models import WorkItem


def submit_task(title: str, body: str, repo: str) -> WorkItem:
    import uuid

    from .runners import validate_repo_url

    validate_repo_url(repo)
    if not title:
        raise ValueError("title required")
    if len(body) > 50000:
        raise ValueError("body too large")
    return WorkItem(
        id=f"wi-{uuid.uuid4().hex[:8]}", source="mcp", title=title, body=body, repo=repo
    )


def describe_tools() -> list[dict]:
    return [
        {
            "name": "submit_task",
            "description": "Submit a task to the factory coordinator",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "repo": {"type": "string"},
                },
                "required": ["title", "repo"],
            },
        },
        {
            "name": "get_status",
            "description": "Get work-item status by id (via GET /v1/work-items/:id)",
            "inputSchema": {
                "type": "object",
                "properties": {"work_item_id": {"type": "string"}},
                "required": ["work_item_id"],
            },
        },
        {
            "name": "answer_question",
            "description": "Answer a coordinator question via Temporal signal answer_question(question_id, answer)",
            "inputSchema": {
                "type": "object",
                "properties": {"question_id": {"type": "integer"}, "answer": {"type": "string"}},
                "required": ["question_id", "answer"],
            },
        },
    ]
