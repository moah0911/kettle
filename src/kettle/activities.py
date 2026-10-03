"""Temporal activities — side effects only (LLM, K8s, git, provider APIs). All live."""

from __future__ import annotations

import json
import os

from pydantic import ValidationError
from temporalio import activity

from .artifacts import artifact_id, save_artifact
from .coordinator import initial_stage, route_after_triage, select_harness
from .harnesses import get_harness
from .models import (
    ArtifactKind,
    HandoffArtifact,
    ReviewReport,
    ReviewVerdict,
    StoryVerdict,
    TriageVerdict,
    WorkItem,
)
from .prompts import load_definition, skills_for, stage_system_prompt
from .providers import ChatRequest, chat, estimate_cost_usd, model_for
from .runners import (
    apply_job,
    branch_name,
    build_k8s_job,
    clone_repo,
    delete_job,
    stream_job_logs,
    validate_repo_url,
    wait_for_job,
)

# Stages whose output is worth keeping after the conversation moves on.
_ARTIFACT_KIND: dict[str, ArtifactKind] = {
    "triage": "research",
    "story": "story",
    "spec": "plan",
}


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
    runner = (load_definition().runners or {}).get("default")
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
    """Run one stage. Returns branch.

    Read-only stages (triage, spec) never execute: the model output is the
    artifact. Executing stages (implement) run inside a fresh clone, never in
    the worker's own directory.
    """
    import shutil
    import tempfile

    validate_repo_url(repo)
    definition = load_definition()
    model = model_for(stage)
    harness = get_harness(select_harness(stage, model_for("implement"), model_for("review")))
    branch = branch_name("factory/", work_item_id)
    logs = _runner_logs(
        work_item_id=work_item_id,
        stage=stage,
        agent=stage,
        model=model,
        repo=repo,
        branch=branch,
        skills=skills_for(stage, definition),
    )
    agent_output = chat(
        ChatRequest(
            model=model,
            system=stage_system_prompt(
                stage,
                defn=definition,
                context=f"Repository: {repo}\nBranch: {branch}\nWork item: {work_item_id}",
            ),
            user=(
                f"Run the {stage} stage for work item {work_item_id}.\n\n"
                f"Runner job logs:\n{logs[:4000]}"
            ),
        )
    )
    tmpdir = tempfile.mkdtemp(prefix="kettle-stage-")
    try:
        workdir: str | None = None
        if harness.executes:
            workdir = clone_repo(repo=repo, dest=tmpdir, token=os.getenv("GITHUB_TOKEN", ""))
        result = harness.run(
            work_item_id=work_item_id,
            stage=stage,
            repo=repo,
            branch=branch,
            prompt=agent_output,
            workdir=workdir,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if result.tests_exit_code != 0:
        raise RuntimeError(f"harness {harness.name} failed: {result.evidence[:500]}")
    cost = estimate_cost_usd(model, len(agent_output.split()))
    activity.logger.info(f"{result.evidence[:120]} cost~${cost:.4f}")
    if stage in _ARTIFACT_KIND:
        save_artifact(
            HandoffArtifact(
                id=artifact_id(work_item_id, stage),
                kind=_ARTIFACT_KIND[stage],
                work_item_id=work_item_id,
                body=agent_output[:5000],
            )
        )
    return branch


def _runner_logs(
    *,
    work_item_id: str,
    stage: str,
    agent: str,
    model: str,
    repo: str,
    branch: str,
    skills: list[str],
) -> str:
    """Collect runner logs for the stage prompt.

    `RUNNER_BACKEND=kubernetes` runs the stage Job on a cluster and returns its
    logs. Anything else (notably `docker` for local dev, where no cluster
    exists) skips the Job and returns no logs — the stage still runs, locally.
    """
    if os.getenv("RUNNER_BACKEND", "kubernetes") != "kubernetes":
        return ""
    namespace = os.getenv("K8S_NAMESPACE", "kettle")
    image, cpu, memory, timeout = _factory_runner_config()
    spec = build_k8s_job(
        namespace=namespace,
        work_item_id=work_item_id,
        stage=stage,
        agent=agent,
        model=model,
        repo=repo,
        image=image,
        cpu=cpu,
        memory=memory,
        timeout_minutes=timeout,
        skills=skills,
    )
    job_name = spec["metadata"]["name"]
    apply_job(spec)
    try:
        status = wait_for_job(namespace=namespace, name=job_name, timeout_s=timeout * 60)
        if status.get("failed"):
            raise RuntimeError(f"stage job {job_name} failed")
        return stream_job_logs(namespace=namespace, job_name=job_name)
    finally:
        try:
            delete_job(namespace=namespace, name=job_name)
        except Exception as exc:  # noqa: BLE001 — cleanup must not mask the stage result
            activity.logger.warning(f"job cleanup failed for {job_name}: {exc}")


def _extract_json(output: str) -> dict | None:
    """Pull the outermost JSON object out of a model response, prose and all."""
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_review_report(output: str) -> ReviewReport:
    """Structured review parsing. Anything unparseable degrades to request_changes."""
    raw = _extract_json(output)
    if raw is None:
        return ReviewReport.fail_closed()
    try:
        report = ReviewReport(**raw)
    except ValidationError:
        return ReviewReport.fail_closed()
    if report.verdict == ReviewVerdict.APPROVE and report.blocking():
        # An approval carrying blocking findings is a contradiction; fail closed.
        return ReviewReport(
            verdict=ReviewVerdict.REQUEST_CHANGES,
            findings=report.findings,
            unparsed=True,
        )
    return report


@activity.defn
async def run_review(work_item_id: str, repo: str) -> str:
    """Independent review on a different model vendor. Returns a fail-closed verdict."""
    branch = branch_name("factory/", work_item_id)
    definition = load_definition()
    output = chat(
        ChatRequest(
            model=model_for("review"),
            system=stage_system_prompt(
                "review",
                defn=definition,
                context=f"Repository: {repo}\nBranch: {branch}\nWork item: {work_item_id}",
            ),
            user=(
                f"Review branch {branch} in {repo} against the approved story and brief.\n"
                "Cite file:line evidence for every finding."
            ),
        )
    )
    activity.logger.info(output[:120])
    report = parse_review_report(output)
    save_artifact(
        HandoffArtifact(
            id=artifact_id(work_item_id, "review"),
            kind="review",
            work_item_id=work_item_id,
            body=output[:5000],
        )
    )
    return report.verdict.value


@activity.defn
async def run_story(work_item_id: str, title: str, body: str, repo: str) -> dict:
    """Business-ambiguity pass. Emits a story contract a human must approve."""
    output = chat(
        ChatRequest(
            model=model_for("story"),
            system=stage_system_prompt("story", context=f"Repository: {repo}"),
            user=f"Title: {title}\n\nRequest:\n{body[:6000]}",
        )
    )
    raw = _extract_json(output)
    if raw is None:
        raise RuntimeError(f"story agent returned no JSON for {work_item_id}")
    story = StoryVerdict(**raw)
    save_artifact(
        HandoffArtifact(
            id=artifact_id(work_item_id, "story"),
            kind="story",
            work_item_id=work_item_id,
            body=story.model_dump_json(indent=2)[:5000],
        )
    )
    return story.model_dump()


@activity.defn
async def classify_triage(title: str, body: str, labels: list[str]) -> dict:
    """LLM-backed triage verdict (JSON). No heuristic fallback."""
    output = chat(
        ChatRequest(
            model=model_for("triage"),
            system=stage_system_prompt("triage"),
            user=f"Title: {title}\nBody: {body}\nLabels: {labels}",
        )
    )
    parsed = _extract_json(output)
    if parsed is None:
        raise RuntimeError("triage classifier returned no JSON")
    return TriageVerdict(**parsed).model_dump()


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

    from .artifacts import artifact_id, list_for_work_item, read_artifact
    from .handoff import compose_pr_body
    from .store import WorkItemStore
    from .trust import allowed_tool

    if allowed_tool("merge", trusted=True, unattended=False):
        # The one invariant this function exists under. Explicit raise, not
        # assert: asserts vanish under `python -O`, invariants must not.
        raise RuntimeError("refusing handoff: the merge invariant is broken")
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to open the handoff PR")
    branch = branch_name("factory/", work_item_id)
    owner_repo = repo if "/" in repo and "://" not in repo else None
    if owner_repo is None:
        raise RuntimeError(f"repo must be owner/repo for PR creation: {repo!r}")
    base = _factory_base_branch()

    artifacts = list_for_work_item(work_item_id)
    stored_review = read_artifact(artifact_id(work_item_id, "review"))
    report = (
        parse_review_report(stored_review.body)
        if stored_review is not None
        else ReviewReport.fail_closed()
    )
    work_item = WorkItemStore().get(work_item_id)
    if work_item is None:
        # The branch already exists; refusing to open the PR would strand the
        # work. Compose a minimal body instead of losing the handoff.
        activity.logger.info(f"{work_item_id} missing from store; composing minimal PR body")
        work_item = WorkItem(
            id=work_item_id, source="api", title=work_item_id, body="", repo=owner_repo
        )
    body = compose_pr_body(work_item, artifacts, report)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "title": f"Factory: {work_item.title}",
                "head": branch,
                "base": base,
                "draft": True,
                "body": body,
            },
        )
        resp.raise_for_status()
    return branch


def _factory_base_branch() -> str:
    return os.getenv("FACTORY_BASE_BRANCH", "main")
