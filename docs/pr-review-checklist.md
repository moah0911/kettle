# PR review checklist

Every pull request in this repository is reviewed against this list, whether the
reviewer is a human or the `review` agent. It is the source of truth for the
`review` agent's `instructions` and `output_format` in `factory/kettle.yaml` —
change one, change the other.

Findings are grouped by severity. Severity is not taste:

| Severity | Meaning |
|---|---|
| **critical** | Ships broken, leaks data, or breaks an invariant. Blocks merge. |
| **important** | Will cause a defect or a future incident. Blocks merge. |
| **minor** | Worth fixing, safe to defer. Never blocks. Mark opinions as opinions. |

`opinion: true` in the review agent's JSON marks a finding as judgement rather
than fact. Opinion findings never block a merge.

## 1. Scope

- [ ] One clear purpose. A pull request that fixes two things is two pull requests.
- [ ] No unrelated refactoring.
- [ ] No files touched that the brief did not name.
- [ ] The diff does what the PR description says it does.

## 2. Tests

- [ ] New behavior has a test.
- [ ] Changed behavior has a *changed* test — a test that still passes after the
      behavior changed is a bug in the test.
- [ ] Failure paths are tested, not just the happy path.
- [ ] Existing tests still pass. No test was weakened, skipped, or deleted to go green.
- [ ] Test setup uses builders (`_item`, the `conftest.py` helpers), not inline dicts.

## 3. Invariants

These are the rules the codebase refuses to negotiate. A violation is critical.

- [ ] **Nothing merges a pull request.** `trust.allowed_tool("merge")` returns
      `False` unconditionally.
- [ ] **No dev bypass, stub path, or default secret** was introduced. Missing
      config raises; `_require_secret` has no dev mode.
- [ ] **No gate was weakened** to make a test pass.
- [ ] **No secret, token, or raw webhook payload** is logged.
- [ ] **The stage activity sequence** in `workflows.py` is unchanged, or
      `STAGE_SEQUENCE_VERSION` was bumped with a `workflow.version()` patch.
- [ ] **No LLM call** was added to `workflows.py`, `coordinator.py`, or another
      pure module.
- [ ] **No stage policy** leaked into `api.py`.
- [ ] **`store.py` schema** is unchanged, or the missing migration is called out
      loudly in the PR description.

## 4. Architecture

- [ ] Pure policy stayed pure. `coordinator.py`, `scorers.py`, `trust.py`,
      `automations.py`, `factory_check.py` still do no I/O.
- [ ] Side effects stayed in `activities.py`.
- [ ] Existing helpers were reused instead of reimplemented.
- [ ] Agent instructions were changed in `factory/kettle.yaml`, not hardcoded as
      prompt strings in Python.
- [ ] New patterns are thin adapters over an upstream project, not a fork — and
      the module docstring names the pattern it adopts.
- [ ] No new dependency without justification.
- [ ] The stable façade is intact: `models.WorkItem`/`Stage`/`RunRecord`,
      `providers.ChatRequest`, `coordinator.*` signatures,
      `runners.build_k8s_job`, the API routes.

## 5. Security

- [ ] Repo URLs go through `runners.validate_repo_url`. No mutable remote config.
- [ ] Webhook signatures are verified; missing secrets fail closed.
- [ ] The repo is in the factory allowlist.
- [ ] `repo_allowed` cannot be satisfied by an empty allowlist.
- [ ] Database errors are not returned to the client verbatim.
- [ ] Untrusted actors cannot reach write tools — check `trust.allowed_tool`
      against the work item's `trusted` flag.

## 6. Docs and agent config

- [ ] `docs/` updated if a subsystem's behaviour changed.
- [ ] `README.md` claims match reality. It has overstated `deploy/` before.
- [ ] If `factory/kettle.yaml` changed, each touched agent still has
      `instructions`, `must_not`, `output_format`, and skills.
- [ ] Writable agents (`story`, `spec`, `implement`) explicitly forbid merging.
- [ ] The `review` agent still runs on a different model vendor than `implement`.

## 7. Before you merge

- [ ] All six CI checks pass. See `AGENTS.md` §Commands.
- [ ] `kettle check` passes.
- [ ] The PR body carries the acceptance criteria and the review findings. If it
      does not, the handoff is not reviewable — see `handoff.compose_pr_body`.
- [ ] A human has read this checklist. An agent ran it; that is not the same
      thing as approving it, and saying otherwise misstates who decided.

## 8. What a rejection looks like

A `reject` verdict — and any run that exhausts its revision budget — does **not**
open a pull request. `coordinator.handoff_allowed` returns `False`, the workflow
returns at `handoff`, and the branch stays pushed but unproposed.

That is deliberate. A rejection rendered as an ordinary draft PR in a queue is a
rejection nobody reads, and the human merge gate becomes a formality. If you find
a rejected branch worth salvaging, read it directly:

```
GET /v1/work-items/{id}/artifacts
```

The review report is the artifact of kind `review`. Decide from there whether to
open a PR by hand.