"""SQLAlchemy-backed persistence. sqlite file by default, Postgres via DATABASE_URL."""

from __future__ import annotations

import json
import os

from sqlalchemy import JSON, Boolean, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./kettle.db")
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


class Base(DeclarativeBase):
    pass


class WorkItemRow(Base):
    __tablename__ = "work_items"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    factory: Mapped[str] = mapped_column(String(100), default="default")
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    repo: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="intake")
    current_stage: Mapped[str] = mapped_column(String(20), default="intake")
    conversation_id: Mapped[str] = mapped_column(String(100), default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), default="", unique=False)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(20))
    agent: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    evidence_url: Mapped[str] = mapped_column(Text, default="")
    branch: Mapped[str] = mapped_column(String(200), default="")


class ScoreRow(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scorer: Mapped[str] = mapped_column(String(100))
    passed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str] = mapped_column(String(100), default="")


class ScheduleRow(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    cron: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), default="coordinator")
    repo: Mapped[str] = mapped_column(String(300), default="")


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    work_item_id: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int] = mapped_column(default=0)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        Base.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None


def session() -> Session:
    return sessionmaker(bind=get_engine())()


def _row_to_item(row: WorkItemRow):
    from .models import Stage, WorkItem, WorkItemStatus

    return WorkItem(
        id=row.id,
        factory=row.factory,
        source=row.source,  # type: ignore[arg-type]
        source_id=row.source_id,
        title=row.title,
        body=row.body,
        repo=row.repo,
        status=WorkItemStatus(row.status),
        current_stage=Stage(row.current_stage),
        conversation_id=row.conversation_id,
        labels=list(row.labels or []),
        trusted=row.trusted,
    )


class WorkItemStore:
    """DB-backed store. Same put/get/list/update/clear surface as before."""

    def put(self, item, idempotency_key: str = ""):
        from sqlalchemy.exc import IntegrityError

        with session() as s:
            if idempotency_key:
                existing = s.execute(
                    select(WorkItemRow).where(WorkItemRow.idempotency_key == idempotency_key)
                ).scalar_one_or_none()
                if existing is not None:
                    return _row_to_item(existing)
            row = WorkItemRow(
                id=item.id,
                factory=item.factory,
                source=item.source,
                source_id=item.source_id,
                title=item.title,
                body=item.body,
                repo=item.repo,
                status=item.status.value,
                current_stage=item.current_stage.value,
                conversation_id=item.conversation_id,
                labels=list(item.labels),
                trusted=item.trusted,
                idempotency_key=idempotency_key,
            )
            s.add(row)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                if idempotency_key:
                    existing = s.execute(
                        select(WorkItemRow).where(WorkItemRow.idempotency_key == idempotency_key)
                    ).scalar_one_or_none()
                    if existing is not None:
                        return _row_to_item(existing)
                raise
            return item

    def update(self, item):
        with session() as s:
            row = s.get(WorkItemRow, item.id)
            if row is None:
                raise KeyError(f"work item not found: {item.id}")
            row.factory = item.factory
            row.source = item.source
            row.source_id = item.source_id
            row.title = item.title
            row.body = item.body
            row.repo = item.repo
            row.status = item.status.value
            row.current_stage = item.current_stage.value
            row.conversation_id = item.conversation_id
            row.labels = list(item.labels)
            row.trusted = item.trusted
            s.commit()
            return item

    def get(self, item_id: str):
        with session() as s:
            row = s.get(WorkItemRow, item_id)
            return _row_to_item(row) if row else None

    def list(self, stage: str | None = None, *, limit: int = 100, offset: int = 0):
        if limit <= 0 or limit > 500 or offset < 0:
            raise ValueError("bad pagination")
        with session() as s:
            q = select(WorkItemRow).order_by(WorkItemRow.id).limit(limit).offset(offset)
            if stage:
                q = (
                    select(WorkItemRow)
                    .where(WorkItemRow.current_stage == stage)
                    .order_by(WorkItemRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            return [_row_to_item(r) for r in s.execute(q).scalars()]

    def clear(self) -> None:
        with session() as s:
            for table in (WorkItemRow, RunRow, ScoreRow, ScheduleRow, ArtifactRow):
                s.query(table).delete()
            s.commit()


def dump_labels(labels: list[str]) -> str:
    return json.dumps(labels)
