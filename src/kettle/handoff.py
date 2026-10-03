"""Delivery layer — compose the human-facing handoff from stored artifacts.

Pure functions, no I/O. The reviewer is the last gate and, per the economics of
this system, the slowest one; the PR they open has to carry enough to decide
without reconstructing the run by hand. Everything here reads from what the
pipeline already persisted.

Nothing in this module reports cost or test outcomes. Those are not persisted per
work item, and a handoff document that carries invented numbers is worse than
one that carries none.
"""

from __future__ import annotations

import json

from .models import HandoffArtifact, ReviewFinding, ReviewReport, StoryVerdict, WorkItem

# GitHub caps a PR body at 65536 characters. Stay well under it and point at the
# artifact for anything longer, so the document is bounded by construction.
_BODY_BUDGET = 20_000
_SEVERITIES = ("critical", "important", "minor")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _story_section(story: StoryVerdict) -> list[str]:
    lines = ["## Story", "", story.story, "", "### Acceptance criteria", ""]
    lines += [f"- {c}" for c in story.acceptance_criteria]
    if story.edge_cases:
        lines += ["", "### Edge cases considered", ""]
        lines += [f"- {e}" for e in story.edge_cases]
    if story.out_of_scope:
        lines += ["", "### Explicitly out of scope", ""]
        lines += [f"- {o}" for o in story.out_of_scope]
    if story.open_questions:
        lines += ["", "### Open questions the story agent could not resolve", ""]
        lines += [f"- {q}" for q in story.open_questions]
    # Trailing blank so the next heading does not fuse onto the last list item
    lines.append("")
    return lines


def _findings_section(report: ReviewReport) -> list[str]:
    lines = ["## Independent review", ""]
    if report.unparsed:
        lines += [
            "The reviewer did not answer in the expected format, so this section",
            "reports no findings. Treat the review as unperformed, not as passed.",
            "",
        ]
        return lines
    blocking = report.blocking()
    if not blocking:
        lines += ["No critical or important findings.", ""]
    for severity in _SEVERITIES:
        group = [f for f in report.findings if f.severity == severity]
        if not group:
            continue
        lines += [f"### {severity.capitalize()}", ""]
        lines += [_render_finding(f) for f in group]
        lines += [""]
    return lines


def _render_finding(finding: ReviewFinding) -> str:
    # The severity lives in the heading above; repeating it per bullet is noise.
    marker = " _(opinion — safe to ignore)_" if finding.opinion else ""
    return f"- `{finding.ref}` — {finding.detail}{marker}"


def _artifact_pointer(kind: str, artifacts: list[HandoffArtifact]) -> str | None:
    for artifact in artifacts:
        if artifact.kind == kind:
            return artifact.id
    return None


def compose_pr_body(
    work_item: WorkItem,
    artifacts: list[HandoffArtifact],
    review: ReviewReport,
) -> str:
    """Render the draft-PR body. Pure — same inputs always give the same text."""
    by_id = {a.id: a for a in artifacts}
    story = _parse_story(by_id)
    lines = [
        f"# {work_item.title}",
        "",
        f"Work item `{work_item.id}` · source `{work_item.source}`"
        + (f" · labels {', '.join(work_item.labels)}" if work_item.labels else ""),
        "",
        "This pull request was produced by an autonomous pipeline. A human reviews",
        "and merges it. Nothing here was merged automatically.",
        "",
    ]
    if story is not None:
        lines += _story_section(story)
    else:
        lines += [
            "## Story",
            "",
            "_No story artifact was recorded for this work item, so there is no",
            "acceptance-criteria contract to check the diff against._",
            "",
        ]

    spec_id = _artifact_pointer("plan", artifacts)
    if spec_id:
        lines += [
            "## Technical brief",
            "",
            f"Stored as artifact `{spec_id}`. Retrieve it with "
            + f"`GET /v1/work-items/{work_item.id}/artifacts`.",
            "",
        ]
    research_id = _artifact_pointer("research", artifacts)
    if research_id:
        lines += [
            "## Triage research",
            "",
            f"Stored as artifact `{research_id}`.",
            "",
        ]

    lines += _findings_section(review)
    lines += [
        "## How to review",
        "",
        "1. Read the acceptance criteria above and check each one against the diff.",
        "2. Work through `docs/pr-review-checklist.md`.",
        "3. Pull the artifacts for the full brief and research:",
        f"   `GET /v1/work-items/{work_item.id}/artifacts`",
        "",
        "---",
        "",
        "An agent ran the review workflow against `docs/pr-review-checklist.md` and",
        "reported the findings above.",
        "",
        "It did not approve this pull request, and it cannot merge it.",
        "The decision to merge is yours.",
    ]
    body = "\n".join(lines)
    if len(body) <= _BODY_BUDGET:
        return body
    return (
        _clip(body, _BODY_BUDGET - 200)
        + "\n\n_Body truncated. Full artifacts: "
        + f"GET /v1/work-items/{work_item.id}/artifacts_\n"
    )


def _parse_story(by_id: dict[str, HandoffArtifact]) -> StoryVerdict | None:
    for artifact in by_id.values():
        if artifact.kind != "story":
            continue
        try:
            return StoryVerdict(**json.loads(artifact.body))
        except (ValueError, TypeError):
            # A truncated or hand-edited artifact must not blank the whole PR
            return None
    return None
