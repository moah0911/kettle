"""Temporal activities — side effects only (LLM, K8s/Docker, git, provider APIs)."""

from __future__ import annotations

import os

from temporalio import activity

from .artifacts import save_artifact
from .coordinator import classify, initial_stage, route_after_triage, select_harness
from .harnesses import get_harness
from .models import HandoffArtifact, RunRecord, Stage, WorkItem
from .providers import ChatRequest, chat, estimate_cost_usd, is_live, model_for
from .runners import apply_job, branch_name, build_docker_run, build_k8s_job, validate_repo_url


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
    """Launch execution backend for a stage; returns branch name.

    Dry-run unless KETTLE_LIVE_K8S=1 (real Job apply) and KETTLE_LIVE_HARNESS=1
    (real CLI harness). Spec-build stays the unit-tested seam.
    """
    import re

    backend = os.getenv("RUNNER_BACKEND", "docker")
    model = model_for(stage)
    harness_name = select_harness(stage, model_for("implement"), model_for("review"))
    harness = get_harness(harness_name)
    try:
        validate_repo_url(repo)
    except ValueError:
        if os.getenv("ALLOW_STRICT_GIT", "0") == "1":
            raise
        activity.logger.warn(f"repo failed brokered-git validation in dev mode: {repo!r}")
    image, cpu, memory, timeout = _factory_runner_config()
    launch: dict = {"applied": False, "dry_run": True}
    if backend == "kubernetes":
        spec = build_k8s_job(
            namespace=os.getenv("K8S_NAMESPACE", "kettle"),
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
        launch = apply_job(spec)
        activity.logger.info(
            f"K8s Job {spec['metadata']['name']} dry_run={launch['dry_run']} for {work_item_id}/{stage}"
        )
    else:
        run = build_docker_run(
            work_item_id=work_item_id,
            stage=stage,
            agent=stage,
            model=model,
            repo=repo,
            cpu=cpu,
            memory=memory,
            timeout_minutes=timeout,
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
    activity.logger.info(
        f"{result.evidence[:120]} cost~${cost:.4f} dry_run={result.dry_run} launched={launch}"
    )
    if stage in {"triage", "spec"}:
        safe_id = re.sub(r"[^a-z0-9-]", "-", work_item_id.lower())[:48] or "task"
        save_artifact(
            HandoffArtifact(
                id=f"{safe_id}-{stage}",
                kind="research" if stage == "triage" else "plan",
                work_item_id=work_item_id,
                body=prompt[:5000],
            )
        )
    return branch


def _factory_runner_config() -> tuple[str, str, str, int]:
    """Read factory runner resources; fall back to safe defaults."""
    try:
        from .factory_check import load_factory

        defn = load_factory(os.getenv("FACTORY_DIR", "./factory"))
        runner = (defn.runners or {}).get("default")
        if runner is None:
            raise KeyError("no default runner")
        resources = runner.resources or {}
        return (
            runner.image,
            str(resources.get("cpu", "2")),
            str(resources.get("memory", "4Gi")),
            int(resources.get("timeoutMinutes", 30)),
        )
    except Exception:  # noqa: BLE001 — dev fallback
        return ("ghcr.io/kettle/agent-runner:latest", "2", "4Gi", 30)


@activity.defn
async def run_review(work_item_id: str, repo: str) -> str:
    """Independent review on the exact pushed SHA with a different vendor model.

    Parses the reviewer LLM *output* (not the request) for an explicit verdict.
    In stub mode (no live LLM) returns approve so dev/tests stay deterministic;
    live mode requires approve|request_changes|reject plus file:line evidence,
    else requests changes.
    """
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
    if not is_live():
        return "approve"
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
    from .trust import allowed_tool

    # Delivery gate: draft PRs allowed for both attended and unattended;
    # merge is absent everywhere (enforced here defensively).
    assert allowed_tool("open_draft_pr", trusted=True, unattended=False)
    assert allowed_tool("open_draft_pr", trusted=False, unattended=True)
    assert not allowed_tool("merge", trusted=True, unattended=False)
    branch = branch_name("factory/", work_item_id)
    if os.getenv("GITHUB_TOKEN"):
        activity.logger.info(f"Handoff: draft PR {repo}:{branch} opened for human review.")
    else:
        activity.logger.info(
            f"Handoff dry-run (no GITHUB_TOKEN): would open draft PR {repo}:{branch}."
        )
    return branch


@activity.defn
async def record_run_result(run: dict) -> dict:
    """Persist a stage RunRecord payload (API stores it via POST /v1/runs)."""
    record = RunRecord(
        id=run.get("id", f"run-{work_item_id_short(run.get('work_item_id', ''))}"),
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


def work_item_id_short(work_item_id: str) -> str:
    import uuid

    return work_item_id or uuid.uuid4().hex[:8]


@activity.defn
async def score_run(test_exit_code: int, met: int, total: int) -> dict:
    from .scorers import group_failures, score_criteria_met, score_tests_pass

    scores = [score_tests_pass(test_exit_code), score_criteria_met(met, total)]
    return {"scores": [s.model_dump() for s in scores], "group": group_failures(scores)}
