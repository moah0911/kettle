"""Work-source adapters — normalize provider events to work items. No network in v0.1."""

from __future__ import annotations

import hashlib
import hmac
import os

from .models import WorkItem
from .trust import TRUSTED_ROLES


def verify_github_signature(payload: bytes, signature: str) -> bool:
    """HMAC-SHA256 webhook verification. Open mode when no secret configured (dev)."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_slack_signature(payload: bytes, timestamp: str, signature: str) -> bool:
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        return True
    base = f"v0:{timestamp}:".encode() + payload
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
    if author_role not in TRUSTED_ROLES:
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
