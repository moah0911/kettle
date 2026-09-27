"""Temporal activities — side effects only (LLM, K8s/Docker, git, provider APIs)."""

from __future__ import annotations

import os

from temporalio import activity

from .coordinator import initial_stage, route_after_triage
from .models import WorkItem
from .providers import ChatRequest, chat
from .runners import branch_name, build_docker_run, build_k8s_job


@activity.defn
async def decide_initial_stage(title: str, body: str, labels: list[str]) -> str:
    item = WorkItem(
        id="tmp", source="api", title=title or "untitled", body=body, repo="tmp", labels=labels
    )
    return initial_stage(item).value


@activity.defn
async def decide_after_triage(title: str, body: str, labels: list[str], complexity: str) -> str:
    item = WorkItem(
        id="tmp", source="api", title=title or "untitled", body=body, repo="tmp", labels=labels
    )
    return route_after_triage(item, complexity).value


@activity.defn
async def run_stage(work_item_id: str, stage: str, repo: str) -> str:
    """Launch execution backend for a stage; returns branch name."""
    backend = os.getenv("RUNNER_BACKEND", "docker")
    model = os.getenv(f"MODEL_{stage.upper()}", "anthropic/claude-sonnet-4-6")
    if backend == "kubernetes":
        spec = build_k8s_job(
            namespace=os.getenv("K8S_NAMESPACE", "kettle"),
            work_item_id=work_item_id,
            stage=stage,
            agent=stage,
            model=model,
            repo=repo,
        )
        activity.logger.info(f"K8s Job {spec['metadata']['name']} for {work_item_id}/{stage}")
    else:
        run = build_docker_run(
            work_item_id=work_item_id, stage=stage, agent=stage, model=model, repo=repo
        )
        activity.logger.info(f"Docker run {run['env']['BRANCH']} for {work_item_id}/{stage}")
    prompt = chat(
        ChatRequest(
            model=model,
            system=f"You are the {stage} agent.",
            user=f"Work item {work_item_id} in {repo}: run stage {stage}.",
        )
    )
    activity.logger.info(prompt[:120])
    return branch_name("factory/", work_item_id)


@activity.defn
async def run_review(work_item_id: str, repo: str) -> str:
    # v0.1: stub verdict; real reviewer LLM + diff check lands in P2.
    prompt = chat(
        ChatRequest(
            model=os.getenv("MODEL_REVIEW", "openai/gpt-5-codex"),
            system="You are the review agent.",
            user=f"Review branch {branch_name('factory/', work_item_id)} in {repo}.",
        )
    )
    activity.logger.info(prompt[:120])
    return "approve"


@activity.defn
async def open_handoff(work_item_id: str, repo: str) -> str:
    branch = branch_name("factory/", work_item_id)
    activity.logger.info(
        f"Handoff: open draft PR {repo}:{branch} for human review (never auto-merge)."
    )
    return branch


@activity.defn
async def score_run(test_exit_code: int, met: int, total: int) -> dict:
    from .scorers import group_failures, score_criteria_met, score_tests_pass

    scores = [score_tests_pass(test_exit_code), score_criteria_met(met, total)]
    return {"scores": [s.model_dump() for s in scores], "group": group_failures(scores)}
