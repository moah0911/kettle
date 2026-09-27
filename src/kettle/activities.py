"""Temporal activities — side effects only (LLM, K8s, git, provider APIs). All live."""

from __future__ import annotations

import json
import os

from temporalio import activity

from .artifacts import save_artifact
from .coordinator import initial_stage, route_after_triage, select_harness
from .harnesses import get_harness
from .models import HandoffArtifact, RunRecord, Stage, TriageVerdict, WorkItem
from .providers import ChatRequest, chat, estimate_cost_usd, model_for
from .runners import (
    apply_job,
    branch_name,
    build_k8s_job,
    delete_job,
    stream_job_logs,
    validate_repo_url,
    wait_for_job,
)


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


def _factory_runner_config() -> tuple[str, str, str, int]:
    from .factory_check import load_factory

    defn = load_factory(os.getenv("FACTORY_DIR", "./factory"))
    runner = (defn.runners or {}).get("default")
    if runner is None:
        raise RuntimeError("factory has no default runner")
    resources = runner.resources or {}
    return (
        runner.image,
        str(resources["cpu"]),
        str(resources["memory"]),
        int(resources["timeoutMinutes"]),
    )


@activity.defn
async def run_stage(work_item_id: str, stage: str, repo: str) -> str:
    """Apply a K8s Job, wait for completion, stream logs, run the harness. Returns branch."""
    import re

    validate_repo_url(repo)
    namespace = os.getenv("K8S_NAMESPACE", "kettle")
    model = model_for(stage)
    harness = get_harness(select_harness(stage, model_for("implement"), model_for("review")))
    image, cpu, memory, timeout = _factory_runner_config()
    spec = build_k8s_job(
        namespace=namespace,
        work_item_id=work_item_id,
        stage=stage,
        agent=stage,
        model=model,
        repo=repo,
        image=image,
        cpu=cpu,
        memory=memory,
        timeout_minutes=timeout,
    )
    job_name = spec["metadata"]["name"]
    apply_job(spec)
    try:
        status = wait_for_job(namespace=namespace, name=job_name, timeout_s=timeout * 60)
        logs = stream_job_logs(namespace=namespace, job_name=job_name)
    finally:
        delete_job(namespace=namespace, name=job_name)
    if status.get("failed"):
        raise RuntimeError(f"stage job {job_name} failed")
    branch = branch_name("factory/", work_item_id)
    agent_output = chat(
        ChatRequest(
            model=model,
            system=f"You are the {stage} agent.",
            user=f"Work item {work_item_id} in {repo}: run stage {stage}.\nJob logs:\n{logs[:4000]}",
        )
    )
    result = harness.run(
        work_item_id=work_item_id, stage=stage, repo=repo, branch=branch, prompt=agent_output
    )
    if result.tests_exit_code != 0:
        raise RuntimeError(f"harness {harness.name} failed: {result.evidence[:500]}")
    cost = estimate_cost_usd(model, len(agent_output.split()))
    activity.logger.info(f"{result.evidence[:120]} cost~${cost:.4f} job={job_name}")
    if stage in {"triage", "spec"}:
        safe_id = re.sub(r"[^a-z0-9-]", "-", work_item_id.lower())[:48] or "task"
        save_artifact(
            HandoffArtifact(
                id=f"{safe_id}-{stage}",
                kind="research" if stage == "triage" else "plan",
                work_item_id=work_item_id,
                body=agent_output[:5000],
            )
        )
    return branch


@activity.defn
async def run_review(work_item_id: str, repo: str) -> str:
    """Independent review: reviewer LLM must return a verdict plus file:line evidence."""
    import re

    model = model_for("review")
    output = chat(
        ChatRequest(
            model=model,
            system=(
                "You are the review agent. Judge each acceptance criterion "
                "with file:line evidence. Reply approve|request_changes|reject."
            ),
            user=f"Review branch {branch_name('factory/', work_item_id)} in {repo}.",
        )
    )
    activity.logger.info(output[:120])
    text = output.lower()
    has_evidence = bool(re.search(r"[\w/.-]+:\d+", output))
    if "request_changes" in text or "request-changes" in text:
        return "request_changes"
    if text.strip().startswith("reject") or "\nreject" in text:
        return "reject"
    if "approve" in text and has_evidence:
        return "approve"
    return "request_changes"


@activity.defn
async def classify_triage(title: str, body: str, labels: list[str]) -> dict:
    """LLM-backed triage verdict (JSON). No heuristic fallback."""
    output = chat(
        ChatRequest(
            model=model_for("triage"),
            system=(
                "You are the triage classifier. Reply ONLY with JSON: "
                '{"type":"bug|feature|chore|question|invalid","priority":"p0|p1|p2|p3",'
                '"complexity":"xs|s|m|l|xl","area":"","actionable":true,"reason":"..."}'
            ),
            user=f"Title: {title}\nBody: {body}\nLabels: {labels}",
        )
    )
    return TriageVerdict(**json.loads(output)).model_dump()


@activity.defn
async def check_dor(title: str, body: str, labels: list[str], triage: dict) -> dict:
    from .coordinator import check_definition_of_ready

    item = WorkItem(
        id="tmp", source="api", title=title or "untitled", body=body, repo="tmp", labels=labels
    )
    return check_definition_of_ready(item, TriageVerdict(**triage)).model_dump()


@activity.defn
async def open_handoff(work_item_id: str, repo: str) -> str:
    """Open a draft PR via the GitHub API. Requires GITHUB_TOKEN. Never merges."""
    import httpx

    from .trust import allowed_tool

    assert allowed_tool("open_draft_pr", trusted=True, unattended=False)
    assert allowed_tool("open_draft_pr", trusted=False, unattended=True)
    assert not allowed_tool("merge", trusted=True, unattended=False)
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to open the handoff PR")
    branch = branch_name("factory/", work_item_id)
    owner_repo = repo if "/" in repo and "://" not in repo else None
    if owner_repo is None:
        raise RuntimeError(f"repo must be owner/repo for PR creation: {repo!r}")
    base = _factory_base_branch()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "title": f"Factory: {work_item_id}",
                "head": branch,
                "base": base,
                "draft": True,
                "body": f"Automated factory handoff for {work_item_id}. Human review required.",
            },
        )
        resp.raise_for_status()
    return branch


def _factory_base_branch() -> str:
    return os.getenv("FACTORY_BASE_BRANCH", "main")


@activity.defn
async def record_run_result(run: dict) -> dict:
    """Validate and return a stage RunRecord payload for POST /v1/runs."""
    record = RunRecord(
        id=run.get("id", f"run-{run.get('work_item_id', '')}"),
        work_item_id=run.get("work_item_id", ""),
        stage=Stage(run.get("stage", "building")),
        agent=run.get("agent", run.get("stage", "implement")),
        model=run.get("model", ""),
        status=run.get("status", "succeeded"),
        cost_usd=float(run.get("cost_usd", 0.0)),
        evidence_url=run.get("evidence_url", ""),
        branch=run.get("branch", ""),
    )
    return record.model_dump()


@activity.defn
async def score_run(test_exit_code: int, met: int, total: int, evidence_refs: int = 0) -> dict:
    from .scorers import group_failures, score_criteria_met, score_tests_pass

    scores = [score_tests_pass(test_exit_code), score_criteria_met(met, total, evidence_refs)]
    return {"scores": [s.model_dump() for s in scores], "group": group_failures(scores)}
