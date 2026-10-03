"""Delivery-layer tests: the PR body a human actually reads, and the gate that
decides whether a PR is opened at all.
"""

import asyncio
import json
import types

import pytest
from fastapi.testclient import TestClient

from kettle import activities
from kettle.api import app
from kettle.artifacts import artifact_id, list_for_work_item, save_artifact
from kettle.handoff import compose_pr_body
from kettle.models import (
    HandoffArtifact,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    WorkItem,
)
from tests.conftest import api_headers

client = TestClient(app)


def _item(**kw) -> WorkItem:
    base = {"id": "wi-1", "source": "api", "title": "Add invoice reminders", "repo": "acme/app"}
    base.update(kw)
    return WorkItem(**base)


def _story_artifact() -> HandoffArtifact:
    return HandoffArtifact(
        id=artifact_id("wi-1", "story"),
        kind="story",
        work_item_id="wi-1",
        body=json.dumps(
            {
                "story": "As an admin I want overdue reminders, so customers get chased.",
                "acceptance_criteria": [
                    "Reminder sent after 7 days unpaid",
                    "Manual trigger rejects cross-tenant access",
                ],
                "edge_cases": ["retries are idempotent"],
                "out_of_scope": ["SMS reminders"],
                "open_questions": [],
            }
        ),
    )


def _clean_report(**kw) -> ReviewReport:
    return ReviewReport(verdict=ReviewVerdict.APPROVE, findings=[], **kw)


# --- artifact ids --------------------------------------------------------------


def test_artifact_id_sanitizes_and_caps():
    artifact = artifact_id("WI_ABC/123", "story")
    assert artifact == "wi-abc-123-story"
    assert artifact_id("x" * 200, "plan") == f"{'x' * 48}-plan"


def test_artifact_id_falls_back_for_empty_input():
    assert artifact_id("___", "review") == "task-review"


# --- the PR body ----------------------------------------------------------------


def test_compose_pr_body_carries_acceptance_criteria():
    body = compose_pr_body(_item(), [_story_artifact()], _clean_report())
    # Without this the reviewer has nothing to check the diff against
    assert "Reminder sent after 7 days unpaid" in body
    assert "As an admin I want overdue reminders" in body
    assert "### Edge cases considered" in body
    assert "### Explicitly out of scope" in body


def test_compose_pr_body_states_human_owns_the_decision():
    body = compose_pr_body(_item(), [], _clean_report())
    assert "cannot merge it" in body
    assert "The decision to merge is yours." in body


def test_compose_pr_body_groups_findings_by_severity():
    report = ReviewReport(
        verdict=ReviewVerdict.REQUEST_CHANGES,
        findings=[
            ReviewFinding(severity="critical", ref="app/api.py:14", detail="tenant check missing"),
            ReviewFinding(
                severity="minor",
                ref="app/ui.py:9",
                detail="could reuse RelativeTime",
                opinion=True,
            ),
        ],
    )
    body = compose_pr_body(_item(), [], report)
    assert "### Critical" in body
    assert "`app/api.py:14`" in body
    assert "### Minor" in body
    # Opinions are labelled so a reviewer can safely skip them
    assert "_(opinion — safe to ignore)_" in body


def test_compose_pr_body_separates_headings_from_lists():
    # A heading flush against a list item does not render as a heading in markdown
    body = compose_pr_body(_item(), [_story_artifact()], _clean_report())
    assert "SMS reminders\n\n## " in body


def test_compose_pr_body_cites_framework_dynamic_routes():
    # The factory reviews other people's repos; `app/[id]/route.ts:4` must be citable
    report = ReviewReport(
        verdict=ReviewVerdict.REQUEST_CHANGES,
        findings=[
            ReviewFinding(
                severity="critical",
                ref="app/api/[id]/remind/route.ts:14",
                detail="no tenant check",
            )
        ],
    )
    body = compose_pr_body(_item(), [], report)
    assert "`app/api/[id]/remind/route.ts:14`" in body


def test_compose_pr_body_flags_unparsed_review():
    report = ReviewReport.fail_closed()
    body = compose_pr_body(_item(), [], report)
    assert "Treat the review as unperformed, not as passed." in body


def test_compose_pr_body_points_at_artifacts():
    artifacts = [
        _story_artifact(),
        HandoffArtifact(id="wi-1-plan", kind="plan", work_item_id="wi-1", body="# Brief"),
    ]
    body = compose_pr_body(_item(), artifacts, _clean_report())
    assert "Stored as artifact `wi-1-plan`" in body
    assert "GET /v1/work-items/wi-1/artifacts" in body


def test_compose_pr_body_survives_a_corrupt_story_artifact():
    # A truncated or hand-edited artifact must not blank the PR
    broken = HandoffArtifact(id="wi-1-story", kind="story", work_item_id="wi-1", body="{trunc")
    body = compose_pr_body(_item(), [broken], _clean_report())
    assert "No story artifact was recorded" in body


def test_compose_pr_body_is_bounded():
    huge = HandoffArtifact(
        id="wi-1-story",
        kind="story",
        work_item_id="wi-1",
        body=json.dumps(
            {
                "story": "As an admin I want reminders",
                "acceptance_criteria": [f"criterion {i} " + "padding " * 200 for i in range(200)],
            }
        ),
    )
    body = compose_pr_body(_item(), [huge], _clean_report())
    assert len(body) < 21_000
    assert "Body truncated" in body


# --- the gate: a rejection opens no PR ------------------------------------------


def _fake_github(monkeypatch) -> dict:
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return types.SimpleNamespace(raise_for_status=lambda: None)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return captured


def test_open_handoff_sends_a_reviewable_body(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    from kettle.store import WorkItemStore

    created = client.post(
        "/v1/work-items",
        json={
            "title": "Add invoice reminders",
            "repo": "acme/app",
            "source": "api",
            "body": "send reminders",
        },
        headers=api_headers(),
    ).json()
    item_id = created["id"]
    save_artifact(
        HandoffArtifact(
            id=artifact_id(item_id, "story"),
            kind="story",
            work_item_id=item_id,
            body=json.dumps(
                {
                    "story": "As an admin I want overdue reminders.",
                    "acceptance_criteria": ["Reminder sent after 7 days unpaid"],
                }
            ),
        )
    )
    save_artifact(
        HandoffArtifact(
            id=artifact_id(item_id, "review"),
            kind="review",
            work_item_id=item_id,
            body=json.dumps(
                {
                    "verdict": "approve",
                    "findings": [
                        {
                            "severity": "minor",
                            "ref": "src/app.py:9",
                            "detail": "minor cleanup",
                            "opinion": True,
                        }
                    ],
                }
            ),
        )
    )
    captured = _fake_github(monkeypatch)
    asyncio.run(activities.open_handoff(item_id, "acme/app"))
    sent = captured["json"]
    assert sent["draft"] is True
    # The real work-item title, not the bare id
    assert sent["title"] == "Factory: Add invoice reminders"
    # Not the old one-sentence placeholder
    assert "Automated factory handoff" not in sent["body"]
    assert "Reminder sent after 7 days unpaid" in sent["body"]
    assert "_(opinion — safe to ignore)_" in sent["body"]
    assert "cannot merge it" in sent["body"]
    assert WorkItemStore().get(item_id) is not None


def test_open_handoff_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        asyncio.run(activities.open_handoff("wi-1", "acme/app"))


def test_open_handoff_rejects_non_github_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    with pytest.raises(RuntimeError, match="owner/repo"):
        asyncio.run(activities.open_handoff("wi-1", "https://evil.example.com/x.git"))


# --- the artifacts route closes the write-only table ---------------------------


def test_artifacts_route_requires_auth():
    assert client.get("/v1/work-items/wi-1/artifacts").status_code == 401


def test_artifacts_route_404s_on_unknown_work_item():
    r = client.get("/v1/work-items/nope/artifacts", headers=api_headers())
    assert r.status_code == 404


def test_artifacts_route_returns_saved_artifacts():
    client.post(
        "/v1/work-items",
        json={"title": "t", "repo": "acme/app", "source": "api", "body": "b"},
        headers=api_headers(),
    )
    from kettle.store import WorkItemStore

    items = WorkItemStore().list()
    saved = save_artifact(
        HandoffArtifact(
            id=artifact_id(items[0].id, "story"), kind="story", work_item_id=items[0].id, body="{}"
        )
    )
    r = client.get(f"/v1/work-items/{items[0].id}/artifacts", headers=api_headers())
    assert r.status_code == 200
    assert saved.id in {a["id"] for a in r.json()}
    assert list_for_work_item(items[0].id)
