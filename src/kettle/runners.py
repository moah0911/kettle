"""Execution plane — pure Job-spec builders. No cluster calls here (those live in activities)."""

from __future__ import annotations

import re

SAFE = re.compile(r"[^a-zA-Z0-9-]+")


def slugify(value: str) -> str:
    return SAFE.sub("-", value).strip("-").lower()[:63] or "task"


def branch_name(prefix: str, work_item_id: str) -> str:
    return f"{prefix}{slugify(work_item_id)}"


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
) -> dict:
    branch = branch_name(branch_prefix, work_item_id)
    job_name = f"kettle-{slugify(work_item_id)}-{slugify(stage)}"
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
) -> dict:
    return {
        "backend": "docker",
        "image": "ghcr.io/kettle/agent-runner:latest",
        "env": {
            "WORK_ITEM_ID": work_item_id,
            "STAGE": stage,
            "AGENT": agent,
            "MODEL": model,
            "REPO": repo,
            "BRANCH": branch_name(branch_prefix, work_item_id),
        },
    }
