"""Cron dispatcher — lists schedules from the API and dispatches due work."""

from __future__ import annotations

import os

import httpx


def main() -> int:
    api = os.getenv("KETTLE_API_URL", "http://kettle-api.kettle.svc:8000")
    key = os.getenv("KETTLE_API_KEY", "")
    if not key:
        raise RuntimeError("KETTLE_API_KEY is required")
    headers = {"X-API-Key": key}
    schedules = httpx.get(f"{api}/v1/schedules", headers=headers, timeout=30).json()
    for schedule in schedules:
        resp = httpx.post(
            f"{api}/v1/work-items",
            headers=headers,
            timeout=30,
            json={
                "source": "cron",
                "title": schedule["name"],
                "body": f"scheduled: {schedule['cron']}",
                "repo": schedule.get("repo", ""),
                "source_id": schedule["id"],
            },
        )
        resp.raise_for_status()
        item_id = resp.json()["id"]
        dispatch = httpx.post(
            f"{api}/v1/work-items/{item_id}/dispatch", headers=headers, timeout=30
        )
        dispatch.raise_for_status()
        print(f"dispatched {item_id} for schedule {schedule['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
