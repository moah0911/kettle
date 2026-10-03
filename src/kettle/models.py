"""Core domain models — pure Pydantic, no I/O. Deterministic and test-friendly."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Stage(str, Enum):
    INTAKE = "intake"
    TRIAGE = "triage"
    STORY = "story"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    HANDOFF = "handoff"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class WorkItemStatus(str, Enum):
    INTAKE = "intake"
    TRIAGE = "triage"
    STORY = "story"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    HANDOFF = "handoff"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class WorkItem(BaseModel):
    id: str = Field(min_length=1)
    factory: str = "default"
    source: Literal["github", "slack", "linear", "jira", "webhook", "api", "mcp", "cron"] = "api"
    source_id: str = ""
    title: str = Field(min_length=1)
    body: str = ""
    repo: str = Field(min_length=1)
    status: WorkItemStatus = WorkItemStatus.INTAKE
    current_stage: Stage = Stage.INTAKE
    conversation_id: str = ""
    labels: list[str] = Field(default_factory=list)
    trusted: bool = False  # stamped at intake; unattended runs get limited writes

    @model_validator(mode="after")
    def _sync_status(self) -> WorkItem:
        # Single truth is current_stage; keep legacy status in sync.
        try:
            self.status = WorkItemStatus(self.current_stage.value)
        except ValueError:
            pass
        return self


class StageDecision(BaseModel):
    next_stage: Stage
    reason: str


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class RunRecord(BaseModel):
    id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    stage: Stage
    agent: str = Field(min_length=1)
    model: str = Field(min_length=1)
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    cost_usd: float = Field(default=0.0, ge=0.0)
    evidence_url: str = ""
    branch: str = ""
    verdict: ReviewVerdict | None = None


ArtifactKind = Literal["research", "story", "plan", "diff", "review"]


class HandoffArtifact(BaseModel):
    """Long docs (research memos, plans) pass by ID, not inline — eve pattern."""

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    kind: ArtifactKind = "research"
    work_item_id: str
    body: str = ""
    size_bytes: int = Field(default=0, ge=0)


class TriageVerdict(BaseModel):
    type: Literal["bug", "feature", "chore", "question", "invalid"] = "bug"
    priority: Literal["p0", "p1", "p2", "p3"] = "p2"
    complexity: Literal["xs", "s", "m", "l", "xl"] = "m"
    area: str = ""
    actionable: bool = True
    reason: str = ""


class DefinitionOfReady(BaseModel):
    ready: bool
    missing: list[str] = Field(default_factory=list)


class StoryVerdict(BaseModel):
    """Story contract: business ambiguity resolved before any technical design.

    Five sections, in order. `open_questions` is the escape hatch — an agent that
    cannot resolve a rule must ask here rather than invent an answer downstream.
    """

    story: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    edge_cases: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    def unresolved(self) -> bool:
        return bool(self.open_questions)


class ReviewFinding(BaseModel):
    """One review finding. `ref` must be `path:line` — evidence is not optional.

    Brackets are allowed so framework dynamic routes (`[id]`) stay citable; the
    factory reviews other people's repos, not only Python ones.
    """

    severity: Literal["critical", "important", "minor"]
    ref: str = Field(pattern=r"^[\w./\[\]-]+:\d+")
    detail: str = Field(min_length=1)
    opinion: bool = False

    def blocks_merge(self) -> bool:
        return self.severity in {"critical", "important"} and not self.opinion


class ReviewReport(BaseModel):
    """Structured review output. Replaces substring verdict scanning."""

    verdict: ReviewVerdict
    findings: list[ReviewFinding] = Field(default_factory=list)
    unparsed: bool = False

    def blocking(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.blocks_merge()]

    @classmethod
    def fail_closed(cls) -> ReviewReport:
        """Unparseable or unevidenced review output never reads as approval."""
        return cls(verdict=ReviewVerdict.REQUEST_CHANGES, findings=[], unparsed=True)
