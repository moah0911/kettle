"""Factory MCP server — JSON-RPC stdio transport bound to the API + store."""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx

from .models import WorkItem
from .runners import validate_repo_url


def submit_task(title: str, body: str, repo: str) -> WorkItem:
    from .integrations import repo_allowed

    validate_repo_url(repo)
    if not title:
        raise ValueError("title required")
    if len(body) > 50000:
        raise ValueError("body too large")
    if not repo_allowed(repo, _factory_repos()):
        raise ValueError(f"repo not in factory: {repo}")
    return WorkItem(
        id=f"wi-{uuid.uuid4().hex[:8]}", source="mcp", title=title, body=body, repo=repo
    )


def _factory_repos() -> list[str]:
    from .factory_check import load_factory

    try:
        return load_factory(os.getenv("FACTORY_DIR", "./factory")).repos
    except (OSError, ValueError):
        return []


def _api() -> tuple[str, dict]:
    api = os.getenv("KETTLE_API_URL", "http://localhost:8000")
    key = os.getenv("KETTLE_API_KEY", "")
    if not key:
        raise RuntimeError("KETTLE_API_KEY is required")
    return api, {"X-API-Key": key}


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


def _handle_call(name: str, arguments: dict) -> dict:
    import asyncio

    api, headers = _api()
    if name == "submit_task":
        item = submit_task(
            arguments.get("title", ""), arguments.get("body", ""), arguments.get("repo", "")
        )
        resp = httpx.post(
            f"{api}/v1/work-items",
            headers=headers,
            timeout=30,
            json={
                "source": "mcp",
                "title": item.title,
                "body": item.body,
                "repo": item.repo,
                "source_id": item.id,
            },
        )
        resp.raise_for_status()
        return resp.json()
    if name == "get_status":
        work_item_id = arguments["work_item_id"]
        resp = httpx.get(f"{api}/v1/work-items/{work_item_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    if name == "answer_question":
        from temporalio.client import Client

        async def _signal() -> None:
            client = await Client.connect(os.getenv("TEMPORAL_HOST", ""))
            handle = client.get_workflow_handle(arguments["workflow_id"])
            await handle.signal(
                "answer_question",
                args=[int(arguments["question_id"]), str(arguments["answer"])],
            )

        asyncio.run(_signal())
        return {"ok": True}
    raise ValueError(f"unknown tool: {name}")


def main() -> int:
    """Minimal JSON-RPC loop over stdio: {"id","method":"tools/call","params":{...}}."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            result = _handle_call(msg["params"]["name"], msg["params"].get("arguments", {}))
            sys.stdout.write(json.dumps({"id": msg.get("id"), "result": result}) + "\n")
        except Exception as exc:  # noqa: BLE001 — reported to the caller
            sys.stdout.write(json.dumps({"id": None, "error": str(exc)}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
