"""Reuse-seam tests: DoR, trust, artifacts, harnesses, store, runners, scorers."""

import hashlib
import hmac
import os

import pytest

from kettle.artifacts import clear as clear_artifacts
from kettle.artifacts import read_artifact, save_artifact, valid_id
from kettle.coordinator import check_definition_of_ready, select_harness
from kettle.harnesses import get_harness
from kettle.integrations import verify_github_signature
from kettle.models import HandoffArtifact, TriageVerdict, WorkItem
from kettle.providers import estimate_cost_usd, model_for, vendor_of
from kettle.runners import skill_env, validate_repo_url
from kettle.scorers import (
    BenchmarkResult,
    FrontierEntry,
    compare_benchmarks,
    propose_followup,
    update_frontier,
)
from kettle.store import WorkItemStore
from kettle.trust import allowed_tool


def _item(**kw) -> WorkItem:
    base = {
        "id": "wi-1",
        "source": "github",
        "title": "Fix crash",
        "body": "x" * 300,
        "repo": "acme/app",
    }
    base.update(kw)
    return WorkItem(**base)


def test_dor_gate():
    dor = check_definition_of_ready(
        _item(body="short"), TriageVerdict(actionable=True, complexity="m")
    )
    assert dor.ready is False and "acceptance criteria" in dor.missing
    ok = check_definition_of_ready(
        _item(body="acceptance: ... " + "x" * 300), TriageVerdict(actionable=True, complexity="s")
    )
    assert ok.ready is True


def test_llm_triage_activity(monkeypatch):
    import asyncio

    import kettle.activities as acts

    def fake_chat(req, **kw):
        return (
            '{"type":"bug","priority":"p1","complexity":"m","area":"auth",'
            '"actionable":true,"reason":"fake"}'
        )

    monkeypatch.setattr(acts, "chat", fake_chat)
    out = asyncio.run(acts.classify_triage("Crash", "stack trace " + "x" * 200, ["bug"]))
    assert out["type"] == "bug" and out["actionable"] is True


def test_select_harness_differs():
    assert select_harness("review", "anthropic/claude-sonnet-4-6", "openai/gpt-5-codex") == "codex"
    assert select_harness("review", "openai/gpt-5-codex", "") == "claude-code"
    assert select_harness("implement") == "claude-code"


def test_trust_model():
    assert allowed_tool("merge", True, False) is False
    assert allowed_tool("open_draft_pr", False, True) is True
    assert allowed_tool("update_factory_brain", False, True) is False
    assert allowed_tool("update_factory_brain", True, False) is True


def test_artifacts_by_id():
    clear_artifacts()
    assert valid_id("wi-1-triage") is True
    assert valid_id("bad/id") is False
    a = save_artifact(
        HandoffArtifact(id="wi-1-triage", kind="research", work_item_id="wi-1", body="memo")
    )
    assert a.body == "memo"
    assert read_artifact("wi-1-triage").body == "memo"
    # never overwrite
    save_artifact(
        HandoffArtifact(id="wi-1-triage", kind="research", work_item_id="wi-1", body="other")
    )
    assert read_artifact("wi-1-triage").body == "memo"
    assert read_artifact("bad/id") is None


def test_harness_registry(monkeypatch):
    import kettle.harnesses as h

    def fake_run(cmd, **kw):
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return P()

    monkeypatch.setattr(h.subprocess, "run", fake_run)
    assert get_harness("codex").name == "codex"
    with pytest.raises(ValueError):
        get_harness("nope")
    res = get_harness("shell").run(
        work_item_id="a", stage="building", repo="r", branch="b", prompt="echo hi"
    )
    assert res.branch == "b" and res.tests_exit_code == 0


def test_store_idempotency():
    s = WorkItemStore()
    a = s.put(_item(id="wi-a"), idempotency_key="github:1:issue_labeled")
    b = s.put(_item(id="wi-b"), idempotency_key="github:1:issue_labeled")
    assert a.id == b.id == "wi-a"
    assert len(s.list()) == 1


def test_runners_validate_and_skills():
    assert validate_repo_url("https://github.com/acme/app").endswith(".git")
    assert validate_repo_url("https://github.com/acme/app") == "https://github.com/acme/app.git"
    assert validate_repo_url("acme/app") == "acme/app"
    try:
        validate_repo_url("not a url!!!")
        raise AssertionError("should raise")
    except ValueError:
        pass
    try:
        validate_repo_url("https://evil.com/a/b")
        raise AssertionError("should raise")
    except ValueError:
        pass
    assert skill_env(["a"], [{"name": "github"}])["SKILL_MCP_SERVERS"] == "github"


def test_scorers_benchmark_frontier_followup():
    best = compare_benchmarks(
        [
            BenchmarkResult(config="a", passed=8, total=10, cost_usd=2.0),
            BenchmarkResult(config="b", passed=8, total=10, cost_usd=1.0),
        ]
    )
    assert best.config == "b"
    f = update_frontier([FrontierEntry(name="v1", score=0.8)], FrontierEntry(name="v2", score=0.9))
    assert [e.name for e in f] == ["v2", "v1"]
    assert propose_followup("none", "wi-1", "r") is None
    assert propose_followup("test-failures", "wi-1", "acme/app")["labels"] == [
        "factory",
        "followup",
    ]


def test_providers_models():
    assert vendor_of(model_for("implement")) != vendor_of(model_for("review"))
    assert estimate_cost_usd("anthropic/claude-haiku-4-5", 1000) > 0


def test_webhook_verify_fail_closed():
    # Without secret, verification raises; with secret, bad sig is False.
    old = os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
    try:
        with pytest.raises(RuntimeError):
            verify_github_signature(b"{}", "")
    finally:
        if old is not None:
            os.environ["GITHUB_WEBHOOK_SECRET"] = old
    secret = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
    good = "sha256=" + hmac.new(secret, b"{}", hashlib.sha256).hexdigest()
    assert verify_github_signature(b"{}", good) is True
    assert verify_github_signature(b"{}", "sha256=bad") is False
