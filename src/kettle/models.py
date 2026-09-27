"""Core domain models — pure Pydantic, no I/O. Deterministic and test-friendly."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Stage(str, Enum):
    INTAKE = "intake"
    TRIAGE = "triage"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    HANDOFF = "handoff"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class WorkItemStatus(str, Enum):
    INTAKE = "intake"
    TRIAGE = "triage"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    HANDOFF = "handoff"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class WorkItem(BaseModel):
    id: str = Field(min_length=1)
    factory: str = "default"
    source: str = Field(description="github|slack|linear|jira|webhook|api|mcp|cron")
    source_id: str = ""
    title: str = Field(min_length=1)
    body: str = ""
    repo: str = Field(min_length=1)
    status: WorkItemStatus = WorkItemStatus.INTAKE
    current_stage: Stage = Stage.INTAKE
    conversation_id: str = ""
    labels: list[str] = Field(default_factory=list)
    trusted: bool = False  # stamped at intake; unattended runs get limited writes


class StageDecision(BaseModel):
    next_stage: Stage
    reason: str


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class RunRecord(BaseModel):
    id: str
    work_item_id: str
    stage: Stage
    agent: str
    model: str
    status: str = "pending"
    cost_usd: float = 0.0
    evidence_url: str = ""
    branch: str = ""
    verdict: ReviewVerdict | None = None


class HandoffArtifact(BaseModel):
    """Long docs (research memos, plans) pass by ID, not inline — eve pattern."""

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    kind: str = Field(description="research|plan|diff|review")
    work_item_id: str
    body: str = ""
    size_bytes: int = 0


class TriageVerdict(BaseModel):
    type: str = "bug"  # bug|feature|chore|question|invalid
    priority: str = "p2"  # p0|p1|p2|p3
    complexity: str = "m"  # xs|s|m|l|xl
    area: str = ""
    actionable: bool = True
    reason: str = ""


class DefinitionOfReady(BaseModel):
    ready: bool
    missing: list[str] = Field(default_factory=list)
