"""Persistence abstraction — in-memory now, Postgres-ready interface.

P1 keeps the FastAPI surface unchanged while allowing a SQLAlchemy backend
to be plugged in without touching routes. Idempotency keys prevent double
intake on webhook retries.
"""

from __future__ import annotations

from .models import WorkItem


class WorkItemStore:
    def __init__(self) -> None:
        self._items: dict[str, WorkItem] = {}
        self._idempotency: dict[str, str] = {}

    def put(self, item: WorkItem, idempotency_key: str = "") -> WorkItem:
        if idempotency_key and idempotency_key in self._idempotency:
            existing_id = self._idempotency[idempotency_key]
            existing = self._items.get(existing_id)
            if existing is not None:
                return existing
        self._items[item.id] = item
        if idempotency_key:
            self._idempotency[idempotency_key] = item.id
        return item

    def update(self, item: WorkItem) -> WorkItem:
        """Explicit persist — replaces in-place mutation for Postgres backends."""
        if item.id not in self._items:
            raise KeyError(f"work item not found: {item.id}")
        self._items[item.id] = item
        return item

    def get(self, item_id: str) -> WorkItem | None:
        return self._items.get(item_id)

    def list(self, stage: str | None = None) -> list[WorkItem]:
        if stage:
            return [w for w in self._items.values() if w.current_stage.value == stage]
        return list(self._items.values())

    def clear(self) -> None:
        self._items.clear()
        self._idempotency.clear()
