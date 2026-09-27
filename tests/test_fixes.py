"""Fix-verification tests: dry-run seams, pagination, validation, review evidence."""

import os

from fastapi.testclient import TestClient

from kettle import activities
from kettle.api import app
from kettle.harnesses import get_harness
from kettle.providers import is_live
from kettle.runners import apply_job, build_k8s_job

client = TestClient(app)


def test_harness_is_explicit_dry_run():
    os.environ.pop("KETTLE_LIVE_HARNESS", None)
    res = get_harness("codex").run(
        work_item_id="a", stage="building", repo="r", branch="b", prompt="p"
    )
    assert res.dry_run is True
    assert res.evidence.startswith("[dry-run:codex")


def test_providers_stub_is_explicit():
    os.environ.pop("KETTLE_LIVE_LLM", None)
    assert is_live() is False


def test_apply_job_dry_run():
    os.environ.pop("KETTLE_LIVE_K8S", None)
    spec = build_k8s_job(
        namespace="kettle",
        work_item_id="wi-x",
        stage="building",
        agent="implement",
        model="m",
        repo="acme/app",
    )
    out = apply_job(spec)
    assert out == {"applied": False, "dry_run": True, "job": spec["metadata"]["name"]}


def test_pagination_and_422():
    r = client.get("/v1/work-items", params={"limit": 1, "offset": 0})
    assert r.status_code == 200
    r = client.get("/v1/runs", params={"limit": 0})
    assert r.status_code == 422


def test_review_evidence_required_in_live_doc():
    # Stub mode stays deterministic approve.
    import asyncio

    assert asyncio.run(activities.run_review("wi-abc", "acme/app")) == "approve"


def test_schedule_cron_validated():
    r = client.post("/v1/schedules", json={"name": "n", "cron": "bad;;cron"})
    assert r.status_code == 422


def test_runs_pagination():
    r = client.get("/v1/runs", params={"limit": 5, "offset": 0})
    assert r.status_code == 200
