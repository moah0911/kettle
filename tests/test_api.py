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
    assert r.json()["routed"] is True


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
