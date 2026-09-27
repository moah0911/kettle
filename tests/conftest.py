"""Fake-server fixtures: temp DB + webhook/API secrets. No live network."""

import hashlib
import hmac
import os
import tempfile

import pytest

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["KETTLE_API_KEY"] = "test-key"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-github-secret-0123456789"
os.environ["SLACK_SIGNING_SECRET"] = "test-slack-secret-0123456789abcdef"
os.environ["LINEAR_WEBHOOK_SECRET"] = "test-linear-secret"
os.environ["JIRA_WEBHOOK_SECRET"] = "test-jira-secret"
os.environ["FACTORY_DIR"] = "./factory"

from kettle import store as _store_mod


@pytest.fixture(autouse=True)
def _clean_db():
    _store_mod.reset_engine()
    from kettle.store import WorkItemStore

    WorkItemStore().clear()
    yield
    WorkItemStore().clear()


def api_headers() -> dict:
    return {"X-API-Key": "test-key"}


def github_sig(body: bytes) -> str:
    secret = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def slack_sig(body: bytes, timestamp: str) -> dict:
    secret = os.environ["SLACK_SIGNING_SECRET"].encode()
    base = f"v0:{timestamp}:".encode() + body
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": "v0=" + hmac.new(secret, base, hashlib.sha256).hexdigest(),
    }


def generic_sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
