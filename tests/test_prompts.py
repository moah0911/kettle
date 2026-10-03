"""Knowledge-layer tests: factory config is compiled into prompts, and the
review contract is parsed structurally rather than by substring scan.
"""

import json

import pytest

from kettle.activities import parse_review_report
from kettle.factory_check import check_factory, load_factory
from kettle.models import ReviewVerdict, StoryVerdict
from kettle.prompts import (
    compose_system_prompt,
    load_definition,
    require_agent,
    skills_for,
    stage_system_prompt,
)


def _defn():
    return load_factory("./factory")


# --- the knowledge layer is actually delivered ---------------------------------


def test_stage_prompt_carries_boundaries_and_contract():
    # Arrange / Act
    prompt = stage_system_prompt("implement", context="Repository: acme/app")
    # Assert: an agent knows its purpose, its limits, and what to hand back
    assert "## Boundaries — never do these" in prompt
    assert "Never merge the pull request" in prompt
    assert "## Output format" in prompt
    assert "Repository: acme/app" in prompt
    assert "You are the implement agent" in prompt


def test_stage_prompt_includes_declared_skills():
    assert "- writing-quality" in stage_system_prompt("implement")


def test_compose_omits_empty_sections():
    # A reviewer with no skills should not get an empty Skills heading
    from kettle.factory_check import AgentConfig

    agent = AgentConfig(model="x/y", instructions="Do the thing.", must_not=["Never merge."])
    prompt = compose_system_prompt(agent, stage="review")
    assert "## Skills" not in prompt
    assert "## Boundaries — never do these" in prompt


def test_require_agent_fails_closed_for_unconfigured_stage():
    # An unconfigured agent must never run on a guessed prompt
    with pytest.raises(RuntimeError, match="no agent for stage"):
        require_agent("not-a-stage", _defn())


def test_skills_for_falls_back_to_factory_level():
    assert skills_for("coordinator", _defn()) == load_definition().skills


# --- the factory cannot ship agents without boundaries or contracts -----------


def test_check_factory_rejects_agent_without_must_not():
    defn = _defn()
    defn.agents["implement"].must_not = []
    assert any("must_not boundaries required" in e for e in check_factory(defn))


def test_check_factory_rejects_agent_without_output_format():
    defn = _defn()
    defn.agents["spec"].output_format = ""
    assert any("output_format contract required" in e for e in check_factory(defn))


def test_check_factory_requires_story_agent():
    defn = _defn()
    del defn.agents["story"]
    assert any("missing agents" in e for e in check_factory(defn))


def test_check_factory_requires_writable_agents_to_forbid_merging():
    # The no-merge invariant must be stated by the agent, not implied by policy
    defn = _defn()
    defn.agents["implement"].must_not = ["Never refactor unrelated code."]
    assert any("explicitly forbid merging" in e for e in check_factory(defn))


def test_shipped_factory_validates():
    assert check_factory(_defn()) == []


# --- review parsing: structured, evidence-bearing, fail-closed ----------------


def test_parse_review_accepts_clean_approval():
    output = json.dumps({"verdict": "approve", "findings": []})
    report = parse_review_report(output)
    assert report.verdict == ReviewVerdict.APPROVE
    assert report.unparsed is False


def test_parse_review_tolerates_prose_around_json():
    output = 'Here is my review:\n{"verdict": "approve", "findings": []}\nThanks.'
    assert parse_review_report(output).verdict == ReviewVerdict.APPROVE


def test_parse_review_fails_closed_on_prose_only():
    # The old implementation scanned for "approve" and could be talked into it
    report = parse_review_report("Looks good to me, approve away!")
    assert report.verdict == ReviewVerdict.REQUEST_CHANGES
    assert report.unparsed is True


def test_parse_review_fails_closed_on_malformed_json():
    assert parse_review_report("{verdict: approve,}").verdict == ReviewVerdict.REQUEST_CHANGES


def test_parse_review_requires_file_line_evidence():
    # A finding with no file:line fails schema validation
    output = json.dumps(
        {"verdict": "request_changes", "findings": [{"severity": "critical", "detail": "bad"}]}
    )
    report = parse_review_report(output)
    assert report.verdict == ReviewVerdict.REQUEST_CHANGES
    assert report.unparsed is True


def test_parse_review_downgrades_approval_with_blocking_findings():
    # An approval carrying critical findings is a contradiction; fail closed
    output = json.dumps(
        {
            "verdict": "approve",
            "findings": [
                {
                    "severity": "critical",
                    "ref": "app/api/routes.py:14",
                    "detail": "tenant check missing",
                    "opinion": False,
                }
            ],
        }
    )
    report = parse_review_report(output)
    assert report.verdict == ReviewVerdict.REQUEST_CHANGES
    assert len(report.blocking()) == 1


def test_parse_review_keeps_approval_when_findings_are_opinions():
    output = json.dumps(
        {
            "verdict": "approve",
            "findings": [
                {
                    "severity": "minor",
                    "ref": "app/ui/Button.tsx:9",
                    "detail": "could reuse RelativeTime",
                    "opinion": True,
                }
            ],
        }
    )
    report = parse_review_report(output)
    assert report.verdict == ReviewVerdict.APPROVE
    assert report.blocking() == []


def test_parse_review_preserves_rejection():
    output = json.dumps({"verdict": "reject", "findings": []})
    assert parse_review_report(output).verdict == ReviewVerdict.REJECT


# --- the story contract --------------------------------------------------------


def test_story_verdict_requires_acceptance_criteria():
    with pytest.raises(ValueError):
        StoryVerdict(story="As a user I want X", acceptance_criteria=[])


def test_story_verdict_reports_unresolved_questions():
    story = StoryVerdict(
        story="As a admin I want reminders",
        acceptance_criteria=["reminder sent after 7 days"],
        open_questions=["does billing own the subscription?"],
    )
    assert story.unresolved() is True
