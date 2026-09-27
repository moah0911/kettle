"""FastAPI ingress — webhooks, REST API, approvals inbox. Async-first, Pydantic everywhere."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .automations import Automation, match_automation
from .factory_check import FactoryDefinition, load_factory
from .integrations import from_github_issue, from_jira, from_linear, from_slack
from .models import Stage, WorkItem, WorkItemStatus

app = FastAPI(title="kettle", version="0.1.0")

_STORE: dict[str, WorkItem] = {}
_AUTOMATIONS: list[Automation] = [
    Automation(
        name="bug-issues",
        on="github.issue_labeled",
        condition="label == 'factory'",
        action="coordinator",
    ),
    Automation(
        name="ci-fix",
        on="github.check_suite.failed",
        condition="branch startswith 'factory/'",
        action="coordinator",
    ),
]


class CreateWorkItem(BaseModel):
    source: str = "api"
    title: str = Field(min_length=1)
    body: str = ""
    repo: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    source_id: str = ""


class Approval(BaseModel):
    work_item_id: str
    kind: str = "spec"  # spec|question|merge
    approved: bool = True
    note: str = ""


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/v1/work-items", response_model=WorkItem)
def create_work_item(payload: CreateWorkItem) -> WorkItem:
    item = WorkItem(
        id=f"wi-{uuid.uuid4().hex[:8]}",
        source=payload.source,
        source_id=payload.source_id,
        title=payload.title,
        body=payload.body,
        repo=payload.repo,
        labels=payload.labels,
        conversation_id=f"conv-{uuid.uuid4().hex[:8]}",
    )
    _STORE[item.id] = item
    return item


@app.get("/v1/work-items", response_model=list[WorkItem])
def list_work_items(stage: str | None = None) -> list[WorkItem]:
    if stage:
        return [w for w in _STORE.values() if w.current_stage.value == stage]
    return list(_STORE.values())


@app.get("/v1/work-items/{item_id}", response_model=WorkItem)
def get_work_item(item_id: str) -> WorkItem:
    try:
        return _STORE[item_id]
    except KeyError:
        raise HTTPException(404, "work item not found")


@app.post("/v1/approvals")
def record_approval(payload: Approval) -> dict:
    item = _STORE.get(payload.work_item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    # Spec approval unblocks planning->building; merge stays human in git host.
    if payload.kind == "spec" and payload.approved and item.current_stage == Stage.PLANNING:
        item.current_stage = Stage.BUILDING
        item.status = WorkItemStatus.BUILDING
    return {"ok": True, "kind": payload.kind, "approved": payload.approved}


@app.post("/webhooks/github")
def github_webhook(event: dict) -> dict:
    etype = event.get("type", "github.issue_labeled")
    auto = match_automation(etype, event.get("context", {}), _AUTOMATIONS)
    if not auto:
        return {"ok": True, "routed": False}
    ctx = event.get("context", {})
    item = from_github_issue(
        issue_id=str(ctx.get("issue_id", "0")),
        title=ctx.get("title", "untitled"),
        body=ctx.get("body", ""),
        repo=ctx.get("repo", ""),
        labels=ctx.get("labels", []),
        author_role=ctx.get("author_role", "NONE"),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    if not item:
        return {"ok": True, "routed": False, "reason": "untrusted or missing factory label"}
    _STORE[item.id] = item
    return {"ok": True, "routed": True, "work_item_id": item.id, "via": auto.name}


@app.post("/webhooks/slack")
def slack_webhook(event: dict) -> dict:
    item = from_slack(
        channel=event.get("channel", "general"),
        user=event.get("user", "u"),
        text=event.get("text", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    _STORE[item.id] = item
    return {"ok": True, "work_item_id": item.id}


@app.post("/webhooks/linear")
def linear_webhook(event: dict) -> dict:
    item = from_linear(
        issue_id=event.get("issue_id", "lin-0"),
        title=event.get("title", "untitled"),
        body=event.get("body", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    _STORE[item.id] = item
    return {"ok": True, "work_item_id": item.id}


@app.post("/webhooks/jira")
def jira_webhook(event: dict) -> dict:
    item = from_jira(
        key=event.get("key", "J-0"),
        title=event.get("title", "untitled"),
        body=event.get("body", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    _STORE[item.id] = item
    return {"ok": True, "work_item_id": item.id}


@app.post("/webhooks/custom")
def custom_webhook(event: dict) -> dict:
    return create_work_item(
        CreateWorkItem(
            source="webhook",
            title=event.get("title", "webhook task"),
            body=event.get("body", ""),
            repo=event.get("repo", ""),
            labels=event.get("labels", []),
        )
    ).model_dump()


@app.get("/v1/factory")
def factory_info() -> dict:
    try:
        defn: FactoryDefinition = load_factory("./factory")
        return {"name": defn.factory_name, "agents": sorted(defn.agents), "repos": defn.repos}
    except (OSError, ValueError) as exc:
        raise HTTPException(500, str(exc))
