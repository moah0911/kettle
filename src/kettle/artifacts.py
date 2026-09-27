"""Handoff artifacts — DB-backed, long docs travel by ID, never overwritten."""

from __future__ import annotations

import re

from sqlalchemy import select

from .models import HandoffArtifact
from .store import ArtifactRow, session

_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_MAX_BYTES = 200_000


def valid_id(artifact_id: str) -> bool:
    return bool(_ID.match(artifact_id))


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
                kind=existing.kind,  # type: ignore[arg-type]
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
            kind=row.kind,
            work_item_id=row.work_item_id,  # type: ignore[arg-type]
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
                kind=r.kind,
                work_item_id=r.work_item_id,  # type: ignore[arg-type]
                body=r.body,
                size_bytes=r.size_bytes,
            )
            for r in rows
        ]
