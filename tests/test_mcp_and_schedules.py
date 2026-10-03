"""MCP server and cron dispatcher tests. Network is faked at the httpx seam;
Temporal is faked at the client seam. No live server, no live workflow.
"""

import asyncio
import types

import pytest

from kettle import dispatch_schedules, mcp_server


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def test_submit_task_builds_work_item():
    item = mcp_server.submit_task("Add reminders", "body", "acme/app")
    assert item.source == "mcp"
    assert item.repo == "acme/app"


def test_submit_task_rejects_repo_outside_factory():
    # URL shape is valid, but the factory does not own it — the allowlist,
    # not the URL parser, is the trust boundary
    with pytest.raises(ValueError, match="repo not in factory"):
        mcp_server.submit_task("t", "b", "other/repo")


def test_submit_task_rejects_bad_repo():
    with pytest.raises(ValueError, match="bad repo url"):
        mcp_server.submit_task("t", "b", "https://evil.example.com/x.git")


def test_submit_task_rejects_missing_title():
    with pytest.raises(ValueError, match="title required"):
        mcp_server.submit_task("", "b", "acme/app")


def test_submit_task_rejects_oversized_body():
    with pytest.raises(ValueError, match="body too large"):
        mcp_server.submit_task("t", "x" * 50001, "acme/app")


def test_api_requires_key(monkeypatch):
    monkeypatch.setenv("KETTLE_API_KEY", "")
    with pytest.raises(RuntimeError, match="KETTLE_API_KEY"):
        mcp_server._api()


def test_describe_tools_lists_the_three_tools():
    names = {tool["name"] for tool in mcp_server.describe_tools()}
    assert names == {"submit_task", "get_status", "answer_question"}


def test_handle_call_submit_posts_to_api(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, timeout=None, json=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp({"id": "wi-1"})

    monkeypatch.setattr(mcp_server.httpx, "post", fake_post)
    out = mcp_server._handle_call("submit_task", {"title": "t", "body": "b", "repo": "acme/app"})
    # The API's response is passed straight back to the MCP client
    assert out["id"] == "wi-1"
    assert captured["json"]["source"] == "mcp"


def test_handle_call_get_status(monkeypatch):
    monkeypatch.setattr(mcp_server.httpx, "get", lambda *a, **kw: _Resp({"id": "wi-1"}))
    out = mcp_server._handle_call("get_status", {"work_item_id": "wi-1"})
    assert out["id"] == "wi-1"


def test_handle_call_answer_question_signals_with_args(monkeypatch):
    # Regression: the signal was called with two positional arguments, which the
    # Temporal client rejects. Multi-argument signals must use args=[...].
    captured: dict = {}

    class FakeHandle:
        async def signal(self, name, **kwargs):
            captured["name"] = name
            captured["args"] = kwargs.get("args")

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            captured["workflow_id"] = workflow_id
            return FakeHandle()

    class FakeClientClass:
        @staticmethod
        async def connect(host):
            return FakeClient()

    fake_tc = types.ModuleType("temporalio.client")
    fake_tc.Client = FakeClientClass
    monkeypatch.setitem(__import__("sys").modules, "temporalio.client", fake_tc)

    out = mcp_server._handle_call(
        "answer_question", {"workflow_id": "kettle-wi-1", "question_id": "3", "answer": "yes"}
    )
    assert out["ok"] is True
    assert captured["name"] == "answer_question"
    assert captured["args"] == [3, "yes"]
    assert captured["workflow_id"] == "kettle-wi-1"


def test_handle_call_rejects_unknown_tool():
    with pytest.raises(ValueError, match="unknown tool"):
        mcp_server._handle_call("nope", {})


def test_dispatch_schedules_creates_and_dispatches(monkeypatch):
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(f"GET {url}")
        return _Resp([{"id": "sch-1", "name": "nightly", "cron": "0 3 * * *", "repo": "acme/app"}])

    def fake_post(url, headers=None, timeout=None, json=None):
        calls.append(f"POST {url}")
        return _Resp({"id": "wi-9"})

    monkeypatch.setattr(dispatch_schedules.httpx, "get", fake_get)
    monkeypatch.setattr(dispatch_schedules.httpx, "post", fake_post)
    assert dispatch_schedules.main() == 0
    assert any("/dispatch" in c for c in calls)


def test_dispatch_schedules_requires_key(monkeypatch):
    monkeypatch.setenv("KETTLE_API_KEY", "")
    with pytest.raises(RuntimeError, match="KETTLE_API_KEY"):
        dispatch_schedules.main()


def test_worker_registers_story_activity():
    # An activity the workflow calls but the worker never registers fails at
    # runtime, deep inside a workflow. Check it is on the worker's surface.
    from kettle import worker
    from kettle.activities import run_story

    assert worker.run_story is run_story
    assert asyncio.iscoroutinefunction(worker.main)
