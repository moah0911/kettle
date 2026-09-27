"""Temporal activities — side effects only (LLM, K8s/Docker, git, provider APIs)."""

from __future__ import annotations

import os

from temporalio import activity

from .artifacts import save_artifact
from .coordinator import classify, initial_stage, route_after_triage, select_harness
from .harnesses import get_harness
from .models import HandoffArtifact, WorkItem
from .providers import ChatRequest, chat, estimate_cost_usd, model_for
from .runners import branch_name, build_docker_run, build_k8s_job, validate_repo_url


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
    model = model_for(stage)
    harness_name = select_harness(stage, model_for("implement"), model_for("review"))
    harness = get_harness(harness_name)
    try:
        validate_repo_url(repo)
    except ValueError:
        pass  # short owner/repo form allowed in dev
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
        # P3 applies via kubernetes client + streams logs; spec-build is the unit-tested seam.
    else:
        run = build_docker_run(
            work_item_id=work_item_id, stage=stage, agent=stage, model=model, repo=repo
        )
        activity.logger.info(f"Docker run {run['env']['BRANCH']} for {work_item_id}/{stage}")
    branch = branch_name("factory/", work_item_id)
    prompt = chat(
        ChatRequest(
            model=model,
            system=f"You are the {stage} agent.",
            user=f"Work item {work_item_id} in {repo}: run stage {stage}.",
        )
    )
    result = harness.run(
        work_item_id=work_item_id, stage=stage, repo=repo, branch=branch, prompt=prompt
    )
    cost = estimate_cost_usd(model, len(prompt.split()))
    activity.logger.info(f"{result.evidence[:120]} cost~${cost:.4f}")
    if stage in {"triage", "spec"}:
        save_artifact(
            HandoffArtifact(
                id=f"{work_item_id}-{stage}",
                kind="research" if stage == "triage" else "plan",
                work_item_id=work_item_id,
                body=prompt[:5000],
            )
        )
    return branch


@activity.defn
async def run_review(work_item_id: str, repo: str) -> str:
    """Independent review on the exact pushed SHA with a different vendor model."""
    model = model_for("review")
    prompt = chat(
        ChatRequest(
            model=model,
            system=(
                "You are the review agent. Judge each acceptance criterion "
                "with file:line evidence. Reply approve|request_changes|reject."
            ),
            user=f"Review branch {branch_name('factory/', work_item_id)} in {repo}.",
        )
    )
    activity.logger.info(prompt[:120])
    text = prompt.lower()
    if "request_changes" in text or "request-changes" in text:
        return "request_changes"
    if "reject" in text:
        return "reject"
    return "approve"


@activity.defn
async def classify_triage(title: str, body: str, labels: list[str]) -> dict:
    item = WorkItem(
        id="tmp", source="api", title=title or "untitled", body=body, repo="tmp", labels=labels
    )
    return classify(item).model_dump()


@activity.defn
async def check_dor(title: str, body: str, labels: list[str], triage: dict) -> dict:
    from .coordinator import check_definition_of_ready
    from .models import TriageVerdict

    item = WorkItem(
        id="tmp", source="api", title=title or "untitled", body=body, repo="tmp", labels=labels
    )
    return check_definition_of_ready(item, TriageVerdict(**triage)).model_dump()


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
