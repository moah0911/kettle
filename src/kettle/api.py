"""FastAPI ingress — webhooks, REST API, approvals inbox. Async-first, Pydantic everywhere."""

from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .automations import Automation, match_automation
from .factory_check import FactoryDefinition, load_factory
from .integrations import (
    from_github_issue,
    from_jira,
    from_linear,
    from_slack,
    repo_allowed,
    verify_generic_webhook,
    verify_github_signature,
    verify_slack_signature,
)
from .models import RunRecord, Stage, WorkItem, WorkItemStatus
from .store import WorkItemStore
from .trust import allowed_tool

app = FastAPI(title="kettle", version="0.1.0")

_STORE = WorkItemStore()
_RUNS: dict[str, RunRecord] = {}
_SCORES: list[dict] = []
_SCHEDULES: list[dict] = []
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
    work_item_id: str = Field(min_length=1)
    kind: Literal["spec", "question", "merge"] = "spec"
    approved: bool = True
    note: str = Field(default="", max_length=5000)
    actor: str = Field(default="", max_length=200)


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("KETTLE_API_KEY", "")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(401, "invalid api key")


def _factory_repos() -> list[str]:
    try:
        return load_factory(os.getenv("FACTORY_DIR", "./factory")).repos
    except (OSError, ValueError):
        return []


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/v1/work-items", response_model=WorkItem)
def create_work_item(payload: CreateWorkItem, _auth: None = Depends(_require_api_key)) -> WorkItem:
    if not repo_allowed(payload.repo, _factory_repos()):
        raise HTTPException(422, "repo not in factory")
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
    return _STORE.put(
        item, idempotency_key=f"{payload.source}:{payload.source_id}" if payload.source_id else ""
    )


@app.get("/v1/work-items", response_model=list[WorkItem])
def list_work_items(stage: Stage | None = None) -> list[WorkItem]:
    return _STORE.list(stage.value if stage else None)


@app.get("/v1/work-items/{item_id}", response_model=WorkItem)
def get_work_item(item_id: str) -> WorkItem:
    item = _STORE.get(item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    return item


@app.post("/v1/approvals")
def record_approval(payload: Approval, _auth: None = Depends(_require_api_key)) -> dict:
    item = _STORE.get(payload.work_item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    transitioned = False
    # Spec approval unblocks planning->building; merge stays human in git host.
    if payload.kind == "merge":
        return {
            "ok": True,
            "kind": payload.kind,
            "approved": False,
            "transitioned": False,
            "reason": "agents never merge",
        }
    if payload.kind == "spec" and payload.approved and item.current_stage == Stage.PLANNING:
        item.current_stage = Stage.BUILDING
        item.status = WorkItemStatus.BUILDING
        _STORE.update(item)
        transitioned = True
    return {
        "ok": True,
        "kind": payload.kind,
        "approved": payload.approved,
        "transitioned": transitioned,
    }


@app.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    raw = await request.body()
    if not verify_github_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    etype = event.get("type", "github.issue_labeled")
    auto = match_automation(etype, event.get("context", {}), _AUTOMATIONS)
    if not auto:
        return {"ok": True, "routed": False}
    ctx = event.get("context", {})
    if not repo_allowed(ctx.get("repo", ""), _factory_repos()):
        return {"ok": True, "routed": False, "reason": "repo not in factory"}
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
    stored = _STORE.put(item, idempotency_key=f"github:{item.source_id}")
    return {"ok": True, "routed": True, "work_item_id": stored.id, "via": auto.name}


@app.post("/webhooks/slack")
async def slack_webhook(request: Request) -> dict:
    raw = await request.body()
    if not verify_slack_signature(
        raw,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if not repo_allowed(event.get("repo", ""), _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    item = from_slack(
        channel=event.get("channel", "general"),
        user=event.get("user", "u"),
        text=event.get("text", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    key = event.get("event_id", "")
    stored = _STORE.put(item, idempotency_key=f"slack:{key}" if key else "")
    return {"ok": True, "work_item_id": stored.id}


@app.post("/webhooks/linear")
async def linear_webhook(request: Request) -> dict:
    raw = await request.body()
    if not verify_generic_webhook(
        "LINEAR_WEBHOOK_SECRET", raw, request.headers.get("X-Linear-Signature", "")
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if not repo_allowed(event.get("repo", ""), _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    item = from_linear(
        issue_id=event.get("issue_id", "lin-0"),
        title=event.get("title", "untitled"),
        body=event.get("body", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    stored = _STORE.put(item, idempotency_key=f"linear:{item.source_id}")
    return {"ok": True, "work_item_id": stored.id}


@app.post("/webhooks/jira")
async def jira_webhook(request: Request) -> dict:
    raw = await request.body()
    if not verify_generic_webhook(
        "JIRA_WEBHOOK_SECRET", raw, request.headers.get("X-Jira-Signature", "")
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if not repo_allowed(event.get("repo", ""), _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    item = from_jira(
        key=event.get("key", "J-0"),
        title=event.get("title", "untitled"),
        body=event.get("body", ""),
        repo=event.get("repo", ""),
        item_id=f"wi-{uuid.uuid4().hex[:8]}",
    )
    stored = _STORE.put(item, idempotency_key=f"jira:{item.source_id}")
    return {"ok": True, "work_item_id": stored.id}


@app.post("/webhooks/custom")
def custom_webhook(event: dict, _auth: None = Depends(_require_api_key)) -> dict:
    if not repo_allowed(event.get("repo", ""), _factory_repos()):
        raise HTTPException(422, "repo not in factory")
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
        defn: FactoryDefinition = load_factory(os.getenv("FACTORY_DIR", "./factory"))
        return {"name": defn.factory_name, "agents": sorted(defn.agents), "repos": defn.repos}
    except (OSError, ValueError):
        raise HTTPException(500, "factory load failed")


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cron: str = Field(min_length=1, max_length=100)
    action: str = "coordinator"
    repo: str = ""


@app.post("/v1/schedules")
def create_schedule(payload: ScheduleIn, _auth: None = Depends(_require_api_key)) -> dict:
    import re

    if not re.match(r"^[\w/*,\- ]+$", payload.cron):
        raise HTTPException(422, "bad cron expression")
    entry = {"id": f"sch-{uuid.uuid4().hex[:12]}", **payload.model_dump()}
    _SCHEDULES.append(entry)
    return entry


@app.get("/v1/schedules")
def list_schedules() -> list[dict]:
    return _SCHEDULES


@app.post("/v1/runs", response_model=RunRecord)
def record_run(run: RunRecord) -> RunRecord:
    if _STORE.get(run.work_item_id) is None:
        raise HTTPException(422, "unknown work_item_id")
    if run.id in _RUNS:
        raise HTTPException(409, "run id already exists")
    _RUNS[run.id] = run
    return run


@app.get("/v1/runs", response_model=list[RunRecord])
def list_runs(work_item_id: str | None = None) -> list[RunRecord]:
    if work_item_id:
        return [r for r in _RUNS.values() if r.work_item_id == work_item_id]
    return list(_RUNS.values())


@app.get("/v1/runs/{run_id}/logs")
def run_logs(run_id: str) -> dict:
    run = _RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    # P3 streams live Job logs; v0.1 returns the stored evidence pointer.
    return {"run_id": run_id, "branch": run.branch, "evidence_url": run.evidence_url}


class ScoreIn(BaseModel):
    scorer: str = Field(min_length=1, max_length=100)
    passed: bool
    reason: str = Field(default="", max_length=2000)
    run_id: str = Field(default="", max_length=100)


@app.post("/v1/scores")
def record_score(score: ScoreIn) -> dict:
    if len(_SCORES) >= 10000:
        raise HTTPException(429, "score buffer full")
    _SCORES.append(score.model_dump())
    return {"ok": True, "total": len(_SCORES)}


@app.get("/v1/dashboard")
def dashboard() -> dict:
    by_stage: dict[str, int] = {}
    for w in _STORE.list():
        by_stage[w.current_stage.value] = by_stage.get(w.current_stage.value, 0) + 1
    total_cost = sum(r.cost_usd for r in _RUNS.values())
    return {
        "work_items_by_stage": by_stage,
        "runs": len(_RUNS),
        "total_cost_usd": round(total_cost, 4),
        "schedules": len(_SCHEDULES),
    }


@app.post("/v1/work-items/{item_id}/dispatch")
def dispatch_work_item(item_id: str, _auth: None = Depends(_require_api_key)) -> dict:
    """Start/signal the Temporal CoordinatorWorkflow (kron gateway pattern).

    v0.1 returns the workflow ID without requiring a live server when
    `TEMPORAL_HOST` is unset; the worker picks it up when configured.
    """
    item = _STORE.get(item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    if not allowed_tool("open_draft_pr", trusted=item.trusted, unattended=not item.trusted):
        raise HTTPException(403, "delivery not allowed for this work item")
    workflow_id = f"kettle-{item.id}"
    host = os.getenv("TEMPORAL_HOST", "")
    if not host:
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "started": False,
            "reason": "TEMPORAL_HOST unset (dev mode)",
        }
    return {"ok": True, "workflow_id": workflow_id, "started": True}
