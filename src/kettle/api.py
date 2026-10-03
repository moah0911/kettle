"""FastAPI ingress — webhooks, REST API, approvals inbox. All live, no dev bypass."""

from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .artifacts import list_for_work_item
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
from .models import HandoffArtifact, RunRecord, Stage, WorkItem, WorkItemStatus
from .store import Base as StoreBase
from .store import RunRow, ScheduleRow, ScoreRow, WorkItemRow, WorkItemStore, get_engine, session

app = FastAPI(title="kettle", version="0.1.0")

StoreBase.metadata.create_all(get_engine())

_STORE = WorkItemStore()


def _factory_automations() -> list[Automation]:
    """Automations live in factory/kettle.yaml — the same file `kettle check`
    validates. A hardcoded copy here would drift from the validated config, so
    there isn't one: malformed entries are skipped, never matched."""
    from pydantic import ValidationError

    try:
        defn = load_factory(os.getenv("FACTORY_DIR", "./factory"))
    except (OSError, ValueError):
        return []
    out: list[Automation] = []
    for raw in defn.automations:
        try:
            out.append(
                Automation(
                    name=raw.get("name", ""),
                    on=raw.get("on", ""),
                    condition=raw.get("condition", ""),
                    action=raw.get("action", "coordinator"),
                )
            )
        except (ValidationError, AttributeError):
            continue
    return [a for a in out if a.name and a.on]


class CreateWorkItem(BaseModel):
    source: str = "api"
    title: str = Field(min_length=1)
    body: str = ""
    repo: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    source_id: str = ""


class Approval(BaseModel):
    work_item_id: str = Field(min_length=1)
    kind: Literal["story", "spec", "question", "merge"] = "spec"
    approved: bool = True
    note: str = Field(default="", max_length=5000)
    actor: str = Field(default="", max_length=200)
    question_id: int = Field(default=0, ge=0)


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("KETTLE_API_KEY", "")
    if not expected:
        raise HTTPException(401, "KETTLE_API_KEY is not configured")
    if x_api_key != expected:
        raise HTTPException(401, "invalid api key")


def _factory_repos() -> list[str]:
    try:
        return load_factory(os.getenv("FACTORY_DIR", "./factory")).repos
    except (OSError, ValueError):
        return []


_RATE: dict[str, list[float]] = {}


def _rate_limit(request: Request, *, limit: int = 60, window_s: int = 60) -> None:
    """In-process fixed-window limiter. Always on."""
    import time

    key = f"{request.url.path}:{request.client.host if request.client else 'local'}"
    now = time.time()
    hits = [t for t in _RATE.get(key, []) if now - t < window_s]
    if len(hits) >= limit:
        raise HTTPException(429, "rate limit exceeded")
    hits.append(now)
    _RATE[key] = hits[-limit:]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/v1/work-items", response_model=WorkItem)
def create_work_item(
    payload: CreateWorkItem, request: Request, _auth: None = Depends(_require_api_key)
) -> WorkItem:
    _rate_limit(request)
    if not repo_allowed(payload.repo, _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    item = WorkItem(
        id=f"wi-{uuid.uuid4().hex[:8]}",
        source=payload.source,  # type: ignore[arg-type]
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
def list_work_items(
    stage: Stage | None = None,
    limit: int = 100,
    offset: int = 0,
    _auth: None = Depends(_require_api_key),
) -> list[WorkItem]:
    return _STORE.list(stage.value if stage else None, limit=limit, offset=offset)


@app.get("/v1/work-items/{item_id}", response_model=WorkItem)
def get_work_item(item_id: str, _auth: None = Depends(_require_api_key)) -> WorkItem:
    item = _STORE.get(item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    return item


@app.get("/v1/work-items/{item_id}/artifacts", response_model=list[HandoffArtifact])
def list_work_item_artifacts(
    item_id: str, _auth: None = Depends(_require_api_key)
) -> list[HandoffArtifact]:
    """Long docs travel by ID. This is the only way to read them back."""
    if not _STORE.get(item_id):
        raise HTTPException(404, "work item not found")
    return list_for_work_item(item_id)


@app.post("/v1/approvals")
async def record_approval(payload: Approval, _auth: None = Depends(_require_api_key)) -> dict:
    """Record a human decision AND deliver it to the running workflow.

    The DB transition is the record; the Temporal signal is the delivery. The
    response reports both halves honestly: a recorded approval the workflow
    never heard (`signaled: false`) is a stuck work item, not an approved one.
    """
    item = _STORE.get(payload.work_item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    transitioned = False
    signal: str | None = None
    signal_args: list = []
    if payload.kind == "merge":
        return {
            "ok": True,
            "kind": payload.kind,
            "approved": False,
            "transitioned": False,
            "signaled": False,
            "reason": "agents never merge",
        }
    if payload.kind == "story" and item.current_stage == Stage.STORY:
        if not payload.approved:
            return {
                "ok": True,
                "kind": payload.kind,
                "approved": False,
                "transitioned": False,
                "signaled": False,
                "reason": "story refused; workflow parks at handoff",
            }
        item.current_stage = Stage.PLANNING
        item.status = WorkItemStatus.PLANNING
        _STORE.update(item)
        transitioned = True
        signal = "approve_story"
    if payload.kind == "spec" and payload.approved and item.current_stage == Stage.PLANNING:
        item.current_stage = Stage.BUILDING
        item.status = WorkItemStatus.BUILDING
        _STORE.update(item)
        transitioned = True
        signal = "approve_spec"
    if payload.kind == "question" and payload.note:
        # The note IS the answer. The workflow ignores mismatched or empty
        # answers by design, so a stale question_id is a safe no-op.
        signal, signal_args = "answer_question", [payload.question_id, payload.note]
    signaled = await _send_workflow_signal(item.id, signal, signal_args) if signal else False
    return {
        "ok": True,
        "kind": payload.kind,
        "approved": payload.approved,
        "transitioned": transitioned,
        "signaled": signaled,
    }


async def _send_workflow_signal(item_id: str, signal: str, args: list) -> bool:
    """Deliver a signal to `kettle-{item_id}`. Never raises: signal delivery
    must not mask an already-recorded approval."""
    host = os.getenv("TEMPORAL_HOST", "")
    if not host:
        return False
    try:
        from temporalio.client import Client

        client = await Client.connect(host)
        handle = client.get_workflow_handle(f"kettle-{item_id}")
        if args:
            await handle.signal(signal, args=args)
        else:
            await handle.signal(signal)
        return True
    except Exception:  # noqa: BLE001 — reported as signaled:false, see caller
        return False


@app.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    _rate_limit(request)
    raw = await request.body()
    if not verify_github_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if "type" not in event and any(k in event for k in ("action", "issue", "repository", "hook")):
        # A genuine GitHub delivery. It verifies, then matches nothing, which
        # used to return ok:true while dropping the event. Say so loudly.
        raise HTTPException(
            422, "raw GitHub events are not accepted; relay as {type, context} (docs/webhooks.md)"
        )
    etype = event.get("type", "github.issue_labeled")
    auto = match_automation(etype, event.get("context", {}), _factory_automations())
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
    dispatched = await _maybe_dispatch(stored)
    return {
        "ok": True,
        "routed": True,
        "work_item_id": stored.id,
        "via": auto.name,
        "dispatched": dispatched,
    }


@app.post("/webhooks/slack")
async def slack_webhook(request: Request) -> dict:
    _rate_limit(request)
    raw = await request.body()
    if not verify_slack_signature(
        raw,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if event.get("type") == "url_verification" and "challenge" in event:
        # Slack's subscription handshake. Signed like any other delivery, so
        # verification above already ran; echoing enables the subscription.
        return {"challenge": event["challenge"]}
    if isinstance(event.get("event"), dict):
        raise HTTPException(
            422, "raw Slack events are not accepted; relay as the flat envelope (docs/webhooks.md)"
        )
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
    return {"ok": True, "work_item_id": stored.id, "dispatched": await _maybe_dispatch(stored)}


@app.post("/webhooks/linear")
async def linear_webhook(request: Request) -> dict:
    _rate_limit(request)
    raw = await request.body()
    if not verify_generic_webhook(
        "LINEAR_WEBHOOK_SECRET", raw, request.headers.get("X-Linear-Signature", "")
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if "data" in event and "issue_id" not in event:
        raise HTTPException(
            422, "raw Linear events are not accepted; relay as the flat envelope (docs/webhooks.md)"
        )
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
    return {"ok": True, "work_item_id": stored.id, "dispatched": await _maybe_dispatch(stored)}


@app.post("/webhooks/jira")
async def jira_webhook(request: Request) -> dict:
    _rate_limit(request)
    raw = await request.body()
    if not verify_generic_webhook(
        "JIRA_WEBHOOK_SECRET", raw, request.headers.get("X-Jira-Signature", "")
    ):
        raise HTTPException(401, "bad webhook signature")
    event = await request.json()
    if "webhookEvent" in event:
        raise HTTPException(
            422, "raw Jira events are not accepted; relay as the flat envelope (docs/webhooks.md)"
        )
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
    return {"ok": True, "work_item_id": stored.id, "dispatched": await _maybe_dispatch(stored)}


@app.post("/webhooks/custom")
async def custom_webhook(
    event: dict, request: Request, _auth: None = Depends(_require_api_key)
) -> dict:
    if not repo_allowed(event.get("repo", ""), _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    created = create_work_item(
        CreateWorkItem(
            source="webhook",
            title=event.get("title", "webhook task"),
            body=event.get("body", ""),
            repo=event.get("repo", ""),
            labels=event.get("labels", []),
        ),
        request,
        _auth,
    )
    item = _STORE.get(created.id)
    body = created.model_dump()
    body["dispatched"] = await _maybe_dispatch(item) if item else False
    return body


@app.get("/v1/factory")
def factory_info(_auth: None = Depends(_require_api_key)) -> dict:
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
    entry_id = f"sch-{uuid.uuid4().hex[:12]}"
    with session() as s:
        if s.get(ScheduleRow, entry_id) is not None:
            raise HTTPException(409, "schedule id collision, retry")
        s.add(
            ScheduleRow(
                id=entry_id,
                name=payload.name,
                cron=payload.cron,
                action=payload.action,
                repo=payload.repo,
            )
        )
        s.commit()
    return {"id": entry_id, **payload.model_dump()}


@app.get("/v1/schedules")
def list_schedules(_auth: None = Depends(_require_api_key)) -> list[dict]:
    with session() as s:
        rows = s.execute(select(ScheduleRow)).scalars()
        return [
            {"id": r.id, "name": r.name, "cron": r.cron, "action": r.action, "repo": r.repo}
            for r in rows
        ]


@app.post("/v1/runs", response_model=RunRecord)
def record_run(run: RunRecord, _auth: None = Depends(_require_api_key)) -> RunRecord:
    if _STORE.get(run.work_item_id) is None:
        raise HTTPException(422, "unknown work_item_id")
    with session() as s:
        if s.get(RunRow, run.id) is not None:
            raise HTTPException(409, "run id already exists")
        s.add(
            RunRow(
                id=run.id,
                work_item_id=run.work_item_id,
                stage=run.stage.value,
                agent=run.agent,
                model=run.model,
                status=run.status,
                cost_usd=run.cost_usd,
                evidence_url=run.evidence_url,
                branch=run.branch,
            )
        )
        s.commit()
    return run


@app.get("/v1/runs", response_model=list[RunRecord])
def list_runs(
    work_item_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _auth: None = Depends(_require_api_key),
) -> list[RunRecord]:
    if limit <= 0 or limit > 500 or offset < 0:
        raise HTTPException(422, "bad pagination")
    with session() as s:
        q = select(RunRow).order_by(RunRow.id).limit(limit).offset(offset)
        if work_item_id:
            q = (
                select(RunRow)
                .where(RunRow.work_item_id == work_item_id)
                .order_by(RunRow.id)
                .limit(limit)
                .offset(offset)
            )
        out = []
        for r in s.execute(q).scalars():
            out.append(
                RunRecord(
                    id=r.id,
                    work_item_id=r.work_item_id,
                    stage=Stage(r.stage),
                    agent=r.agent,
                    model=r.model,
                    status=r.status,  # type: ignore[arg-type]
                    cost_usd=r.cost_usd,
                    evidence_url=r.evidence_url,
                    branch=r.branch,
                )
            )
        return out


@app.get("/v1/runs/{run_id}/logs")
def run_logs(run_id: str, _auth: None = Depends(_require_api_key)) -> dict:
    from .runners import job_name_for, stream_job_logs

    with session() as s:
        run = s.get(RunRow, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        branch, evidence = run.branch, run.evidence_url
        job_name = job_name_for(run.work_item_id, run.stage)
    namespace = os.getenv("K8S_NAMESPACE", "kettle")
    try:
        logs = stream_job_logs(namespace=namespace, job_name=job_name)
    except Exception as exc:  # noqa: BLE001 — fall back to stored pointer
        return {
            "run_id": run_id,
            "branch": branch,
            "evidence_url": evidence,
            "logs": "",
            "note": f"live log fetch failed: {exc}",
        }
    return {"run_id": run_id, "branch": branch, "evidence_url": evidence, "logs": logs[-8000:]}


class ScoreIn(BaseModel):
    scorer: str = Field(min_length=1, max_length=100)
    passed: bool
    reason: str = Field(default="", max_length=2000)
    run_id: str = Field(default="", max_length=100)


@app.post("/v1/scores")
def record_score(score: ScoreIn, _auth: None = Depends(_require_api_key)) -> dict:
    with session() as s:
        total = s.execute(select(func.count()).select_from(ScoreRow)).scalar() or 0
        if total >= 100000:
            raise HTTPException(429, "score buffer full")
        s.add(
            ScoreRow(
                scorer=score.scorer, passed=score.passed, reason=score.reason, run_id=score.run_id
            )
        )
        s.commit()
        return {"ok": True, "total": total + 1}


@app.get("/v1/dashboard")
def dashboard(_auth: None = Depends(_require_api_key)) -> dict:
    from .scorers import Score, group_failures

    with session() as s:
        by_stage: dict[str, int] = {}
        for stage_val, count in s.execute(
            select(WorkItemRow.current_stage, func.count()).group_by(WorkItemRow.current_stage)
        ):
            by_stage[stage_val] = count
        runs = s.execute(select(func.count()).select_from(RunRow)).scalar() or 0
        total_cost = (
            s.execute(select(func.coalesce(func.sum(RunRow.cost_usd), 0.0))).scalar() or 0.0
        )
        schedules = s.execute(select(func.count()).select_from(ScheduleRow)).scalar() or 0
        recent = s.execute(select(ScoreRow).order_by(ScoreRow.id.desc()).limit(200)).scalars()
        scores = [Score(scorer=r.scorer, passed=r.passed, reason=r.reason) for r in recent]
    failing = [sc for sc in scores if not sc.passed]
    return {
        "work_items_by_stage": by_stage,
        "runs": runs,
        "total_cost_usd": round(float(total_cost), 4),
        "schedules": schedules,
        "failure_summary": {
            "scored": len(scores),
            "failing": len(failing),
            "group": group_failures(scores) if scores else "none",
        },
    }


@app.post("/v1/work-items/{item_id}/dispatch")
async def dispatch_work_item(item_id: str, _auth: None = Depends(_require_api_key)) -> dict:
    """Start the Temporal CoordinatorWorkflow. Always dials the server."""
    item = _STORE.get(item_id)
    if not item:
        raise HTTPException(404, "work item not found")
    if not repo_allowed(item.repo, _factory_repos()):
        raise HTTPException(422, "repo not in factory")
    host = os.getenv("TEMPORAL_HOST", "")
    if not host:
        raise HTTPException(503, "TEMPORAL_HOST is not configured")
    workflow_id = await _start_workflow(item)
    return {"ok": True, "workflow_id": workflow_id, "started": True}


async def _start_workflow(item: WorkItem) -> str:
    """Start the coordinator workflow for an item. Raises on failure."""
    from temporalio.client import Client

    from .workflows import CoordinatorWorkflow

    client = await Client.connect(os.getenv("TEMPORAL_HOST", ""))
    workflow_id = f"kettle-{item.id}"
    await client.start_workflow(
        CoordinatorWorkflow.run,
        args=[item.id, item.title, item.body, item.repo, item.labels],
        id=workflow_id,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "kettle"),
    )
    return workflow_id


async def _maybe_dispatch(item: WorkItem | None) -> bool:
    """Dispatch an intake item when the server is configured, skip otherwise.

    Webhook intake is an event, not a record: something happened, so the
    coordinator should hear about it. Without TEMPORAL_HOST there is no
    workflow to hear, and the item waits for a manual dispatch instead.
    Never raises — a dispatch failure must not mask an accepted intake.
    """
    if item is None:
        return False
    if not repo_allowed(item.repo, _factory_repos()):
        return False
    if not os.getenv("TEMPORAL_HOST", ""):
        return False
    try:
        await _start_workflow(item)
        return True
    except Exception:  # noqa: BLE001 — intake stays accepted, dispatch stays manual
        return False
