"""Live-seam tests with fake servers (monkeypatched subprocess/litellm/k8s)."""

import asyncio
import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

from kettle import activities
from kettle.api import app
from kettle.harnesses import get_harness
from tests.conftest import api_headers

client = TestClient(app)


def test_harness_executes_command(monkeypatch, tmp_path):
    import kettle.harnesses as h

    def fake_run(cmd, **kw):
        class P:
            returncode = 0
            stdout = "hello from harness"
            stderr = ""

        assert cmd[0] in {"codex", "claude-code", "opencode"}
        assert kw.get("cwd") == str(tmp_path)
        return P()

    monkeypatch.setattr(h.subprocess, "run", fake_run)
    res = get_harness("codex").run(
        work_item_id="a",
        stage="building",
        repo="r",
        branch="b",
        prompt="echo hi",
        workdir=str(tmp_path),
    )
    assert res.tests_exit_code == 0
    assert "hello" in res.evidence


def test_harness_unknown_raises():
    import pytest as _p

    with _p.raises(ValueError):
        get_harness("nope")


def test_providers_chat_calls_litellm(monkeypatch):
    import kettle.providers as p

    fake_litellm = types.ModuleType("litellm")

    class Msg:
        content = "approve src/foo.py:123"

    class Choice:
        message = Msg()

    class Resp:
        choices = [Choice()]  # noqa: RUF012 — test fake only

    fake_litellm.completion = lambda **kw: Resp()
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    out = p.chat(p.ChatRequest(model="m", system="s", user="u"))
    assert "approve" in out


def test_apply_job_uses_client(monkeypatch):
    import kettle.runners as r

    class Batch:
        def create_namespaced_job(self, namespace, body):
            class O:
                metadata = type("M", (), {"name": body["metadata"]["name"]})()

            return O()

    fake_client = types.SimpleNamespace(BatchV1Api=lambda: Batch())
    fake_config = types.SimpleNamespace(
        load_incluster_config=lambda: None, load_kube_config=lambda: None
    )
    fake_k8s = types.ModuleType("kubernetes")
    fake_k8s.client = fake_client
    fake_k8s.config = fake_config
    monkeypatch.setitem(sys.modules, "kubernetes", fake_k8s)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)
    spec = r.build_k8s_job(
        namespace="kettle",
        work_item_id="wi-x",
        stage="building",
        agent="implement",
        model="m",
        repo="acme/app",
    )
    out = r.apply_job(spec)
    assert out == {"applied": True, "job": spec["metadata"]["name"]}


def test_pagination_and_422():
    r = client.get("/v1/work-items", params={"limit": 1, "offset": 0}, headers=api_headers())
    assert r.status_code == 200
    r = client.get("/v1/runs", params={"limit": 0}, headers=api_headers())
    assert r.status_code == 422


def test_review_parses_live_output(monkeypatch):
    # The reviewer must answer in the structured contract, not prose
    def fake_chat(req, **kw):
        return json.dumps(
            {
                "verdict": "request_changes",
                "findings": [
                    {
                        "severity": "important",
                        "ref": "src/app.py:10",
                        "detail": "missing test",
                        "opinion": False,
                    }
                ],
            }
        )

    monkeypatch.setattr(activities, "chat", fake_chat)
    assert asyncio.run(activities.run_review("wi-abc", "acme/app")) == "request_changes"


def test_review_fails_closed_on_prose(monkeypatch):
    # A reviewer that ignores its contract must not be able to approve
    def fake_chat(req, **kw):
        return "approve, looks great to me!"

    monkeypatch.setattr(activities, "chat", fake_chat)
    assert asyncio.run(activities.run_review("wi-abc", "acme/app")) == "request_changes"


def test_run_story_returns_contract(monkeypatch):
    def fake_chat(req, **kw):
        assert "Boundaries" in req.system
        return json.dumps(
            {
                "story": "As an admin I want invoice reminders",
                "acceptance_criteria": ["reminder sent after 7 days"],
                "edge_cases": ["retries are idempotent"],
                "out_of_scope": ["SMS reminders"],
                "open_questions": [],
            }
        )

    monkeypatch.setattr(activities, "chat", fake_chat)
    story = asyncio.run(activities.run_story("wi-abc", "Reminders", "Add reminders", "acme/app"))
    assert story["acceptance_criteria"] == ["reminder sent after 7 days"]
    assert story["open_questions"] == []


def test_run_story_raises_without_json(monkeypatch):
    monkeypatch.setattr(activities, "chat", lambda req, **kw: "I cannot help with that")
    with pytest.raises(RuntimeError, match="no JSON"):
        asyncio.run(activities.run_story("wi-abc", "T", "B", "acme/app"))


def test_dispatch_starts_workflow_with_args(monkeypatch):
    # Regression: start_workflow took positional args, which the Temporal client
    # rejects. Multi-argument workflows must be passed via args=[...].
    import temporalio.client as tc

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            return FakeHandle()

        async def start_workflow(self, workflow, **kwargs):
            captured.update(kwargs)
            captured["workflow"] = workflow
            return FakeHandle()

    async def fake_connect(host):
        return FakeClient()

    monkeypatch.setattr(tc.Client, "connect", staticmethod(fake_connect))
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    created = client.post(
        "/v1/work-items",
        json={"source": "api", "title": "Fix crash", "body": "x" * 200, "repo": "acme/app"},
        headers=api_headers(),
    )
    item_id = created.json()["id"]
    resp = client.post(f"/v1/work-items/{item_id}/dispatch", headers=api_headers())
    assert resp.status_code == 200
    assert captured["args"][0] == item_id
    assert captured["task_queue"] == "kettle"


def test_custom_webhook_creates_item():
    # Regression: the route called create_work_item without its Request argument
    r = client.post(
        "/webhooks/custom",
        json={"title": "From webhook", "body": "do the thing", "repo": "acme/app"},
        headers=api_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "From webhook"


def test_custom_webhook_rejects_unknown_repo():
    r = client.post(
        "/webhooks/custom", json={"title": "x", "repo": "other/repo"}, headers=api_headers()
    )
    assert r.status_code == 422


def test_schedule_cron_validated():
    r = client.post("/v1/schedules", json={"name": "n", "cron": "bad;;cron"}, headers=api_headers())
    assert r.status_code == 422


def test_runs_pagination():
    r = client.get("/v1/runs", params={"limit": 5, "offset": 0}, headers=api_headers())
    assert r.status_code == 200


def test_run_logs_uses_shared_job_name(monkeypatch):
    from kettle import runners

    seen: dict = {}

    def fake_logs(*, namespace, job_name, tail_lines=500):
        seen["job_name"] = job_name
        return "log lines"

    monkeypatch.setattr(runners, "stream_job_logs", fake_logs)
    created = client.post(
        "/v1/work-items",
        json={"title": "t", "body": "b", "repo": "acme/app", "source": "api"},
        headers=api_headers(),
    ).json()
    run = client.post(
        "/v1/runs",
        json={
            "id": "run-1",
            "work_item_id": created["id"],
            "stage": "building",
            "agent": "implement",
            "model": "m",
            "status": "succeeded",
        },
        headers=api_headers(),
    ).json()
    r = client.get(f"/v1/runs/{run['id']}/logs", headers=api_headers())
    assert r.status_code == 200
    # Same derivation as the builder — not a second inline format string
    assert seen["job_name"] == runners.job_name_for(created["id"], "building")
    assert r.json()["logs"] == "log lines"


def test_run_logs_404s_on_unknown_run():
    assert client.get("/v1/runs/nope/logs", headers=api_headers()).status_code == 404


def test_dashboard_reports_failure_summary():
    for scorer, passed in [("tests-pass", False), ("tests-pass", True)]:
        client.post(
            "/v1/scores",
            json={"scorer": scorer, "passed": passed, "reason": "x"},
            headers=api_headers(),
        )
    body = client.get("/v1/dashboard", headers=api_headers()).json()
    assert body["failure_summary"]["scored"] == 2
    assert body["failure_summary"]["failing"] == 1
    assert body["failure_summary"]["group"] == "test-failures"


def test_dashboard_without_scores_reports_none_group():
    body = client.get("/v1/dashboard", headers=api_headers()).json()
    assert body["failure_summary"] == {"scored": 0, "failing": 0, "group": "none"}


def test_reads_require_auth():
    assert client.get("/v1/work-items").status_code == 401
    assert client.get("/v1/dashboard").status_code == 401
