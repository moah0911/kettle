"""Work-source adapters — normalize provider events to work items. No network in v0.1."""

from __future__ import annotations

import hashlib
import hmac
import os

from .models import WorkItem
from .trust import TRUSTED_ROLES


def _require_secret(name: str) -> str:
    secret = os.getenv(name, "")
    if not secret:
        raise RuntimeError(f"{name} is required (no dev bypass)")
    return secret


def verify_github_signature(payload: bytes, signature: str) -> bool:
    """HMAC-SHA256 webhook verification. Fail-closed: secret always required."""
    secret = _require_secret("GITHUB_WEBHOOK_SECRET")
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_slack_signature(payload: bytes, timestamp: str, signature: str) -> bool:
    secret = _require_secret("SLACK_SIGNING_SECRET")
    if not timestamp or not signature:
        return False
    try:
        age = abs(__import__("time").time() - int(timestamp))
        if age > 300:
            return False
    except ValueError:
        return False
    base = f"v0:{timestamp}:".encode() + payload
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_generic_webhook(secret_env: str, payload: bytes, signature: str) -> bool:
    """Shared-secret check for Linear/Jira/custom webhooks. Fail-closed."""
    secret = _require_secret(secret_env)
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def repo_allowed(repo: str, allowed: list[str]) -> bool:
    if not allowed:
        return False
    return repo in allowed


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
