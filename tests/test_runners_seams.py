"""Execution-plane tests. The cluster is faked at the kubernetes seam; every
call still goes through the real builders and adapters.
"""

import sys
import types

import pytest

from kettle import runners


def _fake_k8s(monkeypatch, batch=None, core=None):
    fake_client = types.SimpleNamespace(BatchV1Api=lambda: batch, CoreV1Api=lambda: core)
    fake_config = types.SimpleNamespace(
        load_incluster_config=lambda: None, load_kube_config=lambda: None
    )
    fake_k8s = types.ModuleType("kubernetes")
    fake_k8s.client = fake_client
    fake_k8s.config = fake_config
    monkeypatch.setitem(sys.modules, "kubernetes", fake_k8s)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)


def test_skill_env_injects_prompts():
    env = runners.skill_env(["writing-quality", "triaging-issues"])
    assert env["SKILL_PROMPTS"] == "writing-quality,triaging-issues"
    assert env["SKILL_MCP_SERVERS"] == ""


def test_skill_env_lists_mcp_servers():
    env = runners.skill_env([], [{"name": "billing"}])
    assert env["SKILL_MCP_SERVERS"] == "billing"


def test_build_k8s_job_injects_skills_into_env():
    spec = runners.build_k8s_job(
        namespace="kettle",
        work_item_id="wi-1",
        stage="implement",
        agent="implement",
        model="m",
        repo="acme/app",
        skills=["writing-quality"],
    )
    env = {
        item["name"]: item["value"]
        for item in spec["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["SKILL_PROMPTS"] == "writing-quality"


def test_build_k8s_job_rejects_out_of_range_timeout():
    with pytest.raises(ValueError, match="timeout_minutes must be 1..120"):
        runners.build_k8s_job(
            namespace="k",
            work_item_id="wi-1",
            stage="implement",
            agent="implement",
            model="m",
            repo="acme/app",
            timeout_minutes=999,
        )


def test_wait_for_job_returns_on_success(monkeypatch):
    class Batch:
        def read_namespaced_job(self, name, namespace):
            return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=1, failed=0))

    _fake_k8s(monkeypatch, batch=Batch())
    assert runners.wait_for_job(namespace="kettle", name="j") == {
        "succeeded": True,
        "failed": False,
    }


def test_wait_for_job_returns_on_failure(monkeypatch):
    class Batch:
        def read_namespaced_job(self, name, namespace):
            return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=0, failed=1))

    _fake_k8s(monkeypatch, batch=Batch())
    assert runners.wait_for_job(namespace="kettle", name="j") == {
        "succeeded": False,
        "failed": True,
    }


def test_wait_for_job_times_out(monkeypatch):
    # A deadline already in the past means the loop never polls
    class Batch:
        def read_namespaced_job(self, name, namespace):
            return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=0, failed=0))

    _fake_k8s(monkeypatch, batch=Batch())
    with pytest.raises(TimeoutError, match="timed out waiting for job"):
        runners.wait_for_job(namespace="kettle", name="j", timeout_s=-1)


def test_stream_job_logs_requires_a_pod(monkeypatch):
    class Core:
        def list_namespaced_pod(self, namespace, label_selector):
            return types.SimpleNamespace(items=[])

    _fake_k8s(monkeypatch, core=Core())
    with pytest.raises(RuntimeError, match="no pods for job"):
        runners.stream_job_logs(namespace="kettle", job_name="j")


def test_stream_job_logs_reads_first_pod(monkeypatch):
    class Core:
        def list_namespaced_pod(self, namespace, label_selector):
            pod = types.SimpleNamespace(metadata=types.SimpleNamespace(name="j-abc"))
            return types.SimpleNamespace(items=[pod])

        def read_namespaced_pod_log(self, name, namespace, tail_lines):
            return f"logs for {name}"

    _fake_k8s(monkeypatch, core=Core())
    assert runners.stream_job_logs(namespace="kettle", job_name="j") == "logs for j-abc"


def test_delete_job_uses_background_propagation(monkeypatch):
    captured: dict = {}

    class Batch:
        def delete_namespaced_job(self, name, namespace, propagation_policy):
            captured["policy"] = propagation_policy

    _fake_k8s(monkeypatch, batch=Batch())
    runners.delete_job(namespace="kettle", name="j")
    assert captured["policy"] == "Background"


def test_slugify_caps_and_sanitizes():
    slug = runners.slugify("WI_ABC/" + "x" * 100)
    assert len(slug) == 63
    assert slug == slug.strip("-")
    assert "/" not in slug


def test_slugify_falls_back_for_empty_input():
    assert runners.slugify("___") == "task"
