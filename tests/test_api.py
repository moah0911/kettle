import json
import os

from fastapi.testclient import TestClient

from kettle.api import app
from tests.conftest import api_headers, generic_sig, github_sig, slack_sig

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_create_and_get_work_item():
    payload = {
        "title": "Fix login redirect",
        "body": "details",
        "repo": "acme/app",
        "source": "api",
    }
    created = client.post("/v1/work-items", json=payload, headers=api_headers()).json()
    assert created["title"] == payload["title"]
    fetched = client.get(f"/v1/work-items/{created['id']}", headers=api_headers()).json()
    assert fetched["id"] == created["id"]


def test_create_requires_auth():
    r = client.post("/v1/work-items", json={"title": "t", "repo": "acme/app"})
    assert r.status_code == 401


def _story_item() -> str:
    created = client.post(
        "/v1/work-items",
        json={
            "title": "Add reminders",
            "body": "A" * 200,
            "repo": "acme/app",
            "source": "api",
        },
        headers=api_headers(),
    ).json()
    return created["id"]


def test_approval_advances_story_to_planning():
    from kettle.models import Stage, WorkItemStatus
    from kettle.store import WorkItemStore

    item_id = _story_item()
    store = WorkItemStore()
    item = store.get(item_id)
    item.current_stage = Stage.STORY
    item.status = WorkItemStatus.STORY
    store.update(item)

    r = client.post(
        "/v1/approvals",
        json={"work_item_id": item_id, "kind": "story", "approved": True},
        headers=api_headers(),
    )
    assert r.json()["transitioned"] is True
    assert store.get(item_id).current_stage == Stage.PLANNING


def test_approval_refuses_story_without_transitioning():
    from kettle.models import Stage, WorkItemStatus
    from kettle.store import WorkItemStore

    item_id = _story_item()
    store = WorkItemStore()
    item = store.get(item_id)
    item.current_stage = Stage.STORY
    item.status = WorkItemStatus.STORY
    store.update(item)

    r = client.post(
        "/v1/approvals",
        json={"work_item_id": item_id, "kind": "story", "approved": False},
        headers=api_headers(),
    )
    body = r.json()
    assert body["transitioned"] is False
    assert "parks at handoff" in body["reason"]
    assert store.get(item_id).current_stage == Stage.STORY


def test_story_approval_ignored_outside_story_stage():
    # Approving a story for an item that never reached the stage is a no-op
    item_id = _story_item()
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": item_id, "kind": "story", "approved": True},
        headers=api_headers(),
    )
    assert r.json()["transitioned"] is False


def test_merge_approval_is_always_refused():
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": _story_item(), "kind": "merge", "approved": True},
        headers=api_headers(),
    )
    body = r.json()
    assert body["approved"] is False
    assert body["reason"] == "agents never merge"


def _fake_temporal(monkeypatch):
    import temporalio.client as tc

    captured: dict = {}

    class FakeHandle:
        def __init__(self, workflow_id):
            captured["workflow_id"] = workflow_id

        async def signal(self, name, **kwargs):
            captured["signal"] = name
            captured["args"] = kwargs.get("args", [])

    class FakeClient:
        def get_workflow_handle(self, workflow_id):
            return FakeHandle(workflow_id)

    async def fake_connect(host):
        captured["host"] = host
        return FakeClient()

    monkeypatch.setattr(tc.Client, "connect", staticmethod(fake_connect))
    return captured


def _story_stage_item() -> str:
    from kettle.models import Stage, WorkItemStatus
    from kettle.store import WorkItemStore

    item_id = _story_item()
    store = WorkItemStore()
    item = store.get(item_id)
    item.current_stage = Stage.STORY
    item.status = WorkItemStatus.STORY
    store.update(item)
    return item_id


def test_story_approval_signals_the_workflow(monkeypatch):
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    captured = _fake_temporal(monkeypatch)
    item_id = _story_stage_item()
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": item_id, "kind": "story", "approved": True},
        headers=api_headers(),
    )
    body = r.json()
    assert body["transitioned"] is True
    assert body["signaled"] is True
    assert captured["signal"] == "approve_story"
    assert captured["workflow_id"] == f"kettle-{item_id}"


def test_spec_approval_signals_the_workflow(monkeypatch):
    from kettle.models import Stage, WorkItemStatus
    from kettle.store import WorkItemStore

    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    captured = _fake_temporal(monkeypatch)
    item_id = _story_item()
    store = WorkItemStore()
    item = store.get(item_id)
    item.current_stage = Stage.PLANNING
    item.status = WorkItemStatus.PLANNING
    store.update(item)
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": item_id, "kind": "spec", "approved": True},
        headers=api_headers(),
    )
    assert r.json()["signaled"] is True
    assert captured["signal"] == "approve_spec"


def test_question_approval_signals_answer_with_note(monkeypatch):
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    captured = _fake_temporal(monkeypatch)
    item_id = _story_item()
    r = client.post(
        "/v1/approvals",
        json={
            "work_item_id": item_id,
            "kind": "question",
            "approved": True,
            "note": "use postgres",
            "question_id": 2,
        },
        headers=api_headers(),
    )
    assert r.json()["signaled"] is True
    assert captured["signal"] == "answer_question"
    assert captured["args"] == [2, "use postgres"]


def test_question_approval_without_note_signals_nothing(monkeypatch):
    # An empty answer would be ignored by the workflow; don't send it
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    captured = _fake_temporal(monkeypatch)
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": _story_item(), "kind": "question", "approved": True},
        headers=api_headers(),
    )
    assert r.json()["signaled"] is False
    assert "signal" not in captured


def test_approval_without_temporal_reports_unsignaled(monkeypatch):
    # No TEMPORAL_HOST: the DB records the decision, nothing hears it
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": _story_stage_item(), "kind": "story", "approved": True},
        headers=api_headers(),
    )
    body = r.json()
    assert body["transitioned"] is True
    assert body["signaled"] is False


def test_approval_survives_signal_failure(monkeypatch):
    import temporalio.client as tc

    async def broken_connect(host):
        raise ConnectionError("temporal is down")

    monkeypatch.setattr(tc.Client, "connect", staticmethod(broken_connect))
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    r = client.post(
        "/v1/approvals",
        json={"work_item_id": _story_stage_item(), "kind": "story", "approved": True},
        headers=api_headers(),
    )
    body = r.json()
    # The decision is recorded even though delivery failed — and it says so
    assert body["transitioned"] is True
    assert body["signaled"] is False


def test_github_webhook_routes_factory_label():
    event = {
        "type": "github.issue_labeled",
        "context": {
            "issue_id": "7",
            "title": "Bug",
            "body": "x",
            "repo": "acme/app",
            "labels": ["factory"],
            "author_role": "MEMBER",
        },
    }
    raw = json.dumps(event).encode()
    r = client.post(
        "/webhooks/github",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": github_sig(raw)},
    )
    body = r.json()
    assert body["routed"] is True
    # No Temporal configured in tests: accepted but not dispatched
    assert body["dispatched"] is False


def _github_event(**ctx) -> bytes:
    base = {
        "issue_id": "7",
        "title": "Bug",
        "body": "x",
        "repo": "acme/app",
        "labels": ["factory"],
        "author_role": "MEMBER",
    }
    base.update(ctx)
    return json.dumps({"type": "github.issue_labeled", "context": base}).encode()


def test_github_webhook_dispatches_on_match(monkeypatch):
    import temporalio.client as tc

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, workflow, **kwargs):
            captured["workflow"] = workflow
            captured.update(kwargs)
            return FakeHandle()

    async def fake_connect(host):
        return FakeClient()

    monkeypatch.setattr(tc.Client, "connect", staticmethod(fake_connect))
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7233")
    raw = _github_event()
    r = client.post(
        "/webhooks/github",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": github_sig(raw)},
    )
    body = r.json()
    assert body["routed"] is True
    assert body["dispatched"] is True
    assert captured["args"][3] == "acme/app"
    assert captured["task_queue"] == "kettle"


def test_automations_come_from_factory_yaml():
    from kettle.api import _factory_automations

    names = {a.name for a in _factory_automations()}
    # All three YAML automations are live, including one no route hardcodes
    assert {"bug-issues", "mention", "ci-fix"} <= names


def test_automations_empty_when_factory_missing(monkeypatch, tmp_path):
    from kettle.api import _factory_automations

    monkeypatch.setenv("FACTORY_DIR", str(tmp_path))
    assert _factory_automations() == []


def test_dispatch_rejects_repo_outside_factory():
    from kettle.store import WorkItemStore

    item_id = _story_item()
    store = WorkItemStore()
    item = store.get(item_id)
    item.repo = "other/repo"
    store.update(item)
    r = client.post(f"/v1/work-items/{item_id}/dispatch", headers=api_headers())
    assert r.status_code == 422


def test_github_webhook_rejects_raw_provider_payload():
    # A genuine GitHub delivery verifies, then matches nothing. That used to
    # return ok:true while dropping the event; now it says so loudly.
    body = json.dumps({"action": "labeled", "issue": {"number": 7}, "repository": {}}).encode()
    r = client.post(
        "/webhooks/github",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": github_sig(body)},
    )
    assert r.status_code == 422
    assert "relay" in r.json()["detail"]


def test_slack_answers_url_verification():
    import time

    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = client.post(
        "/webhooks/slack",
        content=body,
        headers={"Content-Type": "application/json", **slack_sig(body, str(int(time.time())))},
    )
    assert r.json() == {"challenge": "abc123"}


def test_slack_rejects_raw_event_shape():
    import time

    body = json.dumps({"event": {"type": "message", "text": "hi"}, "event_id": "e"}).encode()
    r = client.post(
        "/webhooks/slack",
        content=body,
        headers={"Content-Type": "application/json", **slack_sig(body, str(int(time.time())))},
    )
    assert r.status_code == 422


def test_linear_and_jira_reject_raw_provider_payloads():
    linear = json.dumps({"action": "create", "data": {"id": "x"}}).encode()
    r = client.post(
        "/webhooks/linear",
        content=linear,
        headers={
            "Content-Type": "application/json",
            "X-Linear-Signature": generic_sig(os.environ["LINEAR_WEBHOOK_SECRET"], linear),
        },
    )
    assert r.status_code == 422

    jira = json.dumps({"webhookEvent": "jira:issue_created", "issue": {}}).encode()
    r = client.post(
        "/webhooks/jira",
        content=jira,
        headers={
            "Content-Type": "application/json",
            "X-Jira-Signature": generic_sig(os.environ["JIRA_WEBHOOK_SECRET"], jira),
        },
    )
    assert r.status_code == 422


def test_github_webhook_rejects_bad_signature():
    r = client.post(
        "/webhooks/github", json={"type": "x"}, headers={"X-Hub-Signature-256": "sha256=bad"}
    )
    assert r.status_code == 401


def test_github_webhook_ignores_untrusted():
    event = {
        "type": "github.issue_labeled",
        "context": {
            "issue_id": "8",
            "title": "Bug",
            "body": "x",
            "repo": "acme/app",
            "labels": ["factory"],
            "author_role": "NONE",
        },
    }
    raw = json.dumps(event).encode()
    res = client.post(
        "/webhooks/github",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": github_sig(raw)},
    ).json()
    assert res["routed"] is False


def test_slack_linear_jira_intake():
    import time

    body = b'{"text": "fix it", "repo": "acme/app", "event_id": "e1"}'
    r = client.post(
        "/webhooks/slack",
        content=body,
        headers={"Content-Type": "application/json", **slack_sig(body, str(int(time.time())))},
    )
    assert "work_item_id" in r.json()

    linear = b'{"title": "t", "repo": "acme/app", "issue_id": "lin-9"}'
    r = client.post(
        "/webhooks/linear",
        content=linear,
        headers={
            "Content-Type": "application/json",
            "X-Linear-Signature": generic_sig(os.environ["LINEAR_WEBHOOK_SECRET"], linear),
        },
    )
    assert "work_item_id" in r.json()

    jira = b'{"title": "t", "repo": "acme/app", "key": "J-9"}'
    r = client.post(
        "/webhooks/jira",
        content=jira,
        headers={
            "Content-Type": "application/json",
            "X-Jira-Signature": generic_sig(os.environ["JIRA_WEBHOOK_SECRET"], jira),
        },
    )
    assert "work_item_id" in r.json()
