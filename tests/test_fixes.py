"""Live-seam tests with fake servers (monkeypatched subprocess/litellm/k8s)."""

import asyncio
import sys
import types

from fastapi.testclient import TestClient

from kettle import activities
from kettle.api import app
from kettle.harnesses import get_harness
from tests.conftest import api_headers

client = TestClient(app)


def test_harness_executes_command(monkeypatch):
    import kettle.harnesses as h

    def fake_run(cmd, **kw):
        class P:
            returncode = 0
            stdout = "hello from harness"
            stderr = ""

        assert cmd[0] in {"sh", "codex", "claude-code", "opencode"}
        return P()

    monkeypatch.setattr(h.subprocess, "run", fake_run)
    res = get_harness("codex").run(
        work_item_id="a", stage="building", repo="r", branch="b", prompt="echo hi"
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
    def fake_chat(req, **kw):
        return "request_changes src/app.py:10 missing test"

    monkeypatch.setattr(activities, "chat", fake_chat)
    assert asyncio.run(activities.run_review("wi-abc", "acme/app")) == "request_changes"


def test_schedule_cron_validated():
    r = client.post("/v1/schedules", json={"name": "n", "cron": "bad;;cron"}, headers=api_headers())
    assert r.status_code == 422


def test_runs_pagination():
    r = client.get("/v1/runs", params={"limit": 5, "offset": 0}, headers=api_headers())
    assert r.status_code == 200


def test_reads_require_auth():
    assert client.get("/v1/work-items").status_code == 401
    assert client.get("/v1/dashboard").status_code == 401
