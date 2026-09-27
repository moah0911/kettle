"""Handoff artifacts — long docs travel by ID, orchestrator relays only the ID."""

from __future__ import annotations

import re

from .models import HandoffArtifact

_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_MAX_BYTES = 200_000
_STORE: dict[str, HandoffArtifact] = {}


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
    if artifact.id in _STORE:
        return _STORE[artifact.id]  # never overwrite
    _STORE[artifact.id] = stored
    return stored


def read_artifact(artifact_id: str) -> HandoffArtifact | None:
    if not valid_id(artifact_id):
        return None
    return _STORE.get(artifact_id)


def clear() -> None:
    _STORE.clear()
