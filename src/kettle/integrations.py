"""Work-source adapters — normalize provider events to work items. No network in v0.1."""

from __future__ import annotations

from .models import WorkItem


def from_github_issue(
    *,
    issue_id: str,
    title: str,
    body: str,
    repo: str,
    labels: list[str],
    author_role: str,
    item_id: str,
) -> WorkItem | None:
    if "factory" not in labels:
        return None
    if author_role not in {"OWNER", "MEMBER", "COLLABORATOR"}:
        return None
    return WorkItem(
        id=item_id,
        source="github",
        source_id=issue_id,
        title=title,
        body=body,
        repo=repo,
        labels=labels,
        trusted=False,
    )


def from_slack(*, channel: str, user: str, text: str, repo: str, item_id: str) -> WorkItem:
    return WorkItem(
        id=item_id,
        source="slack",
        source_id=f"{channel}/{user}",
        title=text[:120] or "Slack request",
        body=text,
        repo=repo,
        trusted=True,
    )


def from_linear(*, issue_id: str, title: str, body: str, repo: str, item_id: str) -> WorkItem:
    return WorkItem(
        id=item_id,
        source="linear",
        source_id=issue_id,
        title=title,
        body=body,
        repo=repo,
        trusted=True,
    )


def from_jira(*, key: str, title: str, body: str, repo: str, item_id: str) -> WorkItem:
    return WorkItem(
        id=item_id, source="jira", source_id=key, title=title, body=body, repo=repo, trusted=True
    )
