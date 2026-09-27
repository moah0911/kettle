from fastapi.testclient import TestClient

from kettle.api import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_create_and_get_work_item():
    # Arrange
    payload = {
        "title": "Fix login redirect",
        "body": "details",
        "repo": "acme/app",
        "source": "api",
    }
    # Act
    created = client.post("/v1/work-items", json=payload).json()
    fetched = client.get(f"/v1/work-items/{created['id']}").json()
    # Assert
    assert created["title"] == payload["title"]
    assert fetched["id"] == created["id"]


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
    res = client.post("/webhooks/github", json=event).json()
    assert res["routed"] is True


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
    res = client.post("/webhooks/github", json=event).json()
    assert res["routed"] is False


def test_slack_linear_jira_intake():
    assert (
        "work_item_id"
        in client.post("/webhooks/slack", json={"text": "fix it", "repo": "acme/app"}).json()
    )
    assert (
        "work_item_id"
        in client.post("/webhooks/linear", json={"title": "t", "repo": "acme/app"}).json()
    )
    assert (
        "work_item_id"
        in client.post("/webhooks/jira", json={"title": "t", "repo": "acme/app"}).json()
    )
