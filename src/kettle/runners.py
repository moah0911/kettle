"""Execution plane — pure Job-spec builders. No cluster calls here (those live in activities)."""

from __future__ import annotations

import re

SAFE = re.compile(r"[^a-zA-Z0-9-]+")
_GH = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$")


def slugify(value: str) -> str:
    return SAFE.sub("-", value).strip("-").lower()[:63] or "task"


def branch_name(prefix: str, work_item_id: str) -> str:
    return f"{prefix}{slugify(work_item_id)}"


def validate_repo_url(url: str) -> str:
    """Brokered git: only validated literal URLs, never mutable remote config.

    Allows `owner/repo` short form and `https://github.com/owner/repo[.git]`.
    Rejects other hosts, ssh/scp syntax, traversal, and blank segments.
    """
    short = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    if short.match(url or ""):
        if ".." in url:
            raise ValueError(f"bad repo url: {url}")
        return url
    if not _GH.match(url or ""):
        raise ValueError(f"bad repo url: {url}")
    if ".." in url:
        raise ValueError(f"bad repo url: {url}")
    if url.endswith(".git"):
        return url
    return url + ".git"


def skill_env(skills: list[str], mcp_servers: list[dict] | None = None) -> dict:
    """Skill injection (kelos/kube-foundry pattern): merged config via env."""
    return {
        "SKILL_PROMPTS": ",".join(skills),
        "SKILL_MCP_SERVERS": ",".join(s.get("name", "") for s in (mcp_servers or [])),
    }


def apply_job(spec: dict) -> dict:
    """Apply a Job spec, or return explicit dry-run when no cluster is configured.

    Real apply happens only when KETTLE_LIVE_K8S=1 and the `kubernetes` client
    is importable; otherwise returns {"applied": False, "dry_run": True}.
    """
    import os

    if os.getenv("KETTLE_LIVE_K8S") != "1":
        return {"applied": False, "dry_run": True, "job": spec["metadata"]["name"]}
    try:
        from kubernetes import client, config  # type: ignore
    except ImportError as exc:
        raise RuntimeError("kubernetes client not installed") from exc
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001 — fall back to kubeconfig
        config.load_kube_config()
    batch = client.BatchV1Api()
    created = batch.create_namespaced_job(namespace=spec["metadata"]["namespace"], body=spec)
    return {"applied": True, "dry_run": False, "job": created.metadata.name}


def build_k8s_job(
    *,
    namespace: str,
    work_item_id: str,
    stage: str,
    agent: str,
    model: str,
    repo: str,
    branch_prefix: str = "factory/",
    image: str = "ghcr.io/kettle/agent-runner:latest",
    cpu: str = "2",
    memory: str = "4Gi",
    timeout_minutes: int = 30,
    skills: list[str] | None = None,
    mcp_servers: list[dict] | None = None,
) -> dict:
    if timeout_minutes <= 0 or timeout_minutes > 120:
        raise ValueError("timeout_minutes must be 1..120")
    branch = branch_name(branch_prefix, work_item_id)
    job_name = f"kettle-{slugify(work_item_id)}-{slugify(stage)}"[:63].rstrip("-")
    extra = skill_env(skills or [], mcp_servers)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {"app": "kettle", "work-item": slugify(work_item_id), "stage": stage},
        },
        "spec": {
            "backoffLimit": 1,
            "activeDeadlineSeconds": timeout_minutes * 60,
            "template": {
                "metadata": {"labels": {"app": "kettle", "work-item": slugify(work_item_id)}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "agent",
                            "image": image,
                            "env": [
                                {"name": "WORK_ITEM_ID", "value": work_item_id},
                                {"name": "STAGE", "value": stage},
                                {"name": "AGENT", "value": agent},
                                {"name": "MODEL", "value": model},
                                {"name": "REPO", "value": repo},
                                {"name": "BRANCH", "value": branch},
                                {"name": "SKILL_PROMPTS", "value": extra["SKILL_PROMPTS"]},
                                {"name": "SKILL_MCP_SERVERS", "value": extra["SKILL_MCP_SERVERS"]},
                            ],
                            "resources": {
                                "limits": {"cpu": cpu, "memory": memory},
                                "requests": {"cpu": cpu, "memory": memory},
                            },
                        }
                    ],
                },
            },
        },
    }


def build_docker_run(
    *,
    work_item_id: str,
    stage: str,
    agent: str,
    model: str,
    repo: str,
    branch_prefix: str = "factory/",
    cpu: str = "2",
    memory: str = "4Gi",
    timeout_minutes: int = 30,
) -> dict:
    if timeout_minutes <= 0 or timeout_minutes > 120:
        raise ValueError("timeout_minutes must be 1..120")
    return {
        "backend": "docker",
        "image": "ghcr.io/kettle/agent-runner:latest",
        "resources": {"cpu": cpu, "memory": memory, "timeoutMinutes": timeout_minutes},
        "env": {
            "WORK_ITEM_ID": work_item_id,
            "STAGE": stage,
            "AGENT": agent,
            "MODEL": model,
            "REPO": repo,
            "BRANCH": branch_name(branch_prefix, work_item_id),
        },
    }
