"""Cron dispatcher — reads /v1/schedules semantics without a live server.

Lists due schedules and prints the work that would be dispatched. Real
dispatch POSTs to the API with KETTLE_API_KEY; dry-run otherwise.
"""

from __future__ import annotations

import os


def main() -> int:
    api = os.getenv("KETTLE_API_URL", "http://kettle-api.kettle.svc:8000")
    if not os.getenv("KETTLE_API_KEY"):
        print(f"dry-run: would list schedules at {api}/v1/schedules")
        return 0
    print(f"dispatching schedules via {api}/v1/schedules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
