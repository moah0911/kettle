"""Handoff artifacts — DB-backed, long docs travel by ID, never overwritten."""

from __future__ import annotations

import re
from typing import get_args

from sqlalchemy import select

from .models import ArtifactKind, HandoffArtifact
from .store import ArtifactRow, session

_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_MAX_BYTES = 200_000

_KINDS: tuple[ArtifactKind, ...] = get_args(ArtifactKind)


def _kind(raw: str) -> ArtifactKind:
    """Narrow a persisted kind back to the Literal.

    Rows written before a kind existed must still be readable, so fall back
    rather than raising — a stale row is not a reason to fail a workflow.
    """
    for kind in _KINDS:
        if kind == raw:
            return kind
    return "research"


def valid_id(artifact_id: str) -> bool:
    return bool(_ID.match(artifact_id))


def artifact_id(work_item_id: str, suffix: str) -> str:
    """Deterministic artifact id for a work item's stage output.

    Work-item ids carry characters the artifact id pattern forbids, so they are
    sanitized and truncated here — the one place that knows the id grammar.
    """
    safe = re.sub(r"[^a-z0-9-]", "-", work_item_id.lower()).strip("-")[:48].strip("-")
    return f"{safe or 'task'}-{suffix}"


def save_artifact(artifact: HandoffArtifact) -> HandoffArtifact:
    if not valid_id(artifact.id):
        raise ValueError(f"bad artifact id: {artifact.id}")
    body = artifact.body[:_MAX_BYTES]
    stored = HandoffArtifact(
        id=artifact.id,
        kind=artifact.kind,
        work_item_id=artifact.work_item_id,
        body=body,
        size_bytes=len(body.encode()),
    )
    with session() as s:
        existing = s.get(ArtifactRow, artifact.id)
        if existing is not None:
            return HandoffArtifact(
                id=existing.id,
                kind=_kind(existing.kind),
                work_item_id=existing.work_item_id,
                body=existing.body,
                size_bytes=existing.size_bytes,
            )
        s.add(
            ArtifactRow(
                id=stored.id,
                kind=stored.kind,
                work_item_id=stored.work_item_id,
                body=stored.body,
                size_bytes=stored.size_bytes,
            )
        )
        s.commit()
    return stored


def read_artifact(artifact_id: str) -> HandoffArtifact | None:
    if not valid_id(artifact_id):
        return None
    with session() as s:
        row = s.get(ArtifactRow, artifact_id)
        if row is None:
            return None
        return HandoffArtifact(
            id=row.id,
            kind=_kind(row.kind),
            work_item_id=row.work_item_id,
            body=row.body,
            size_bytes=row.size_bytes,
        )


def clear() -> None:
    with session() as s:
        s.query(ArtifactRow).delete()
        s.commit()


def list_for_work_item(work_item_id: str) -> list[HandoffArtifact]:
    with session() as s:
        rows = s.execute(
            select(ArtifactRow).where(ArtifactRow.work_item_id == work_item_id)
        ).scalars()
        return [
            HandoffArtifact(
                id=r.id,
                kind=_kind(r.kind),
                work_item_id=r.work_item_id,
                body=r.body,
                size_bytes=r.size_bytes,
            )
            for r in rows
        ]
