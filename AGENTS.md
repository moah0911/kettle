# AGENTS.md

Project instructions for AI coding assistants and humans working on this repository.

This file is deliberately tool-agnostic. If you are Claude Code, Cursor, Codex,
Copilot, opencode, Aider, or a human with a text editor, the rules below are the
same. Nothing here depends on a specific vendor's plugin format.

If you are an AI agent, read this file completely before your first edit. It is
short on purpose — a rule that belongs in a multi-step procedure does not belong
here.

## What this project is

Kettle is an autonomous software development factory. It takes work items from
GitHub, Slack, Linear, Jira, cron, an MCP client, or the REST API, and drives
them through a durable, human-gated pipeline that ends in a **draft** pull
request. A human always merges.

The pipeline:

```
sources -> ingress (FastAPI) -> automations -> coordinator (Temporal workflow)
        -> stage Jobs (K8s/Docker) -> PR handoff -> scorers
```

## Stack

- Python 3.11+
- FastAPI + Pydantic v2 + pydantic-settings
- Temporal (`temporalio`) for durable orchestration
- SQLAlchemy 2.0 async, Postgres in production, SQLite in tests
- Kubernetes Jobs for stage execution; Docker fallback for local dev
- LiteLLM for model access — any OpenAI-compatible provider
- MCP server for local agent clients
- `uv` for dependency management

## Commands

```bash
uv sync --extra dev                                  # install
uv run pytest --cov=kettle tests/ -q                 # tests + coverage
uv run ruff check src tests                          # lint
uv run ruff format --check src tests                 # format check
uv run mypy src                                      # type check
uv run python -m kettle.factory_check --factory ./factory   # validate agent config
uv run uvicorn kettle.api:app --reload --port 8000   # API
uv run python -m kettle.worker                       # Temporal worker
uv run temporal server start-dev                      # local Temporal
docker compose up --build                            # full local stack
```

All six of the check commands above run in CI. A change is not done until every
one of them passes locally.

## Architecture — the rules that matter

**Pure policy is separated from side effects.** This is the central invariant.
`coordinator.py`, `scorers.py`, `trust.py`, `automations.py`, and
`factory_check.py` contain no I/O. All I/O lives in `activities.py`. This is
what keeps the Temporal workflow deterministic — a workflow that did network
calls would not replay.

- Business and routing policy → `coordinator.py`, as pure functions.
- Side effects (LLM, K8s, git, provider APIs) → `activities.py`, only.
- Workflow state machine and human gates → `workflows.py`.
- HTTP, auth, rate limiting → `api.py`. No stage policy here.
- Model names, pricing, cost estimation → `providers.py`. Only place.
- Agent instructions and contracts → `factory/kettle.yaml`, never inline.

**Reuse-first, adapter-first.** Patterns are adopted from upstream projects
through thin adapters rather than vendored. Each module docstring names the
pattern it implements (`kron-k8s-agent`, `eve`, `ai-sdlc`, `kelos`, `snowl`).
Read `docs/architecture.md` before adding a new subsystem.

**The reviewer is the bottleneck, so the handoff must be self-contained.**
`handoff.compose_pr_body` is pure and builds the draft-PR body from stored
artifacts — story, acceptance criteria, review findings by severity. Long docs
travel by artifact ID, never inlined. If you add a field a reviewer would need,
add it there. Never replace the composed body with a placeholder string: a
one-sentence PR forces the human to reconstruct the whole run by hand.

**Artifacts are a read path, not a write path.** Everything `activities.py`
saves must be reachable via `GET /v1/work-items/{id}/artifacts`. A table nothing
reads is the same failure as a prompt nothing receives.

**The stable façade.** These names are depended on externally; changing their
shape is a breaking change:

- `models.WorkItem`, `models.Stage`, `models.RunRecord`
- `providers.ChatRequest`
- `coordinator.*` function signatures
- `runners.build_k8s_job`
- `handoff.compose_pr_body`
- the API routes and `factory/kettle.yaml`

## Agent definitions

Agents are declared in `factory/kettle.yaml`, not in code and not in
tool-specific directories. Each agent declares:

| Field | Purpose |
|---|---|
| `instructions` | What the agent is for and how it works |
| `must_not` | Boundaries — the most-skipped field in agent definitions |
| `output_format` | The contract the next agent in the chain relies on |
| `skills` | Procedures injected as `SKILL_PROMPTS` |
| `model`, `harness` | Execution config |

`kettle check` fails CI if any agent is missing `must_not` or `output_format`,
and writable agents (`story`, `spec`, `implement`) must explicitly forbid
merging.

**When you add or change an agent, change the YAML — never the prompt strings in
`activities.py`.** Prompts are compiled from the factory config by
`prompts.py`. If you find yourself writing a multi-line system prompt in
Python, it belongs in `kettle.yaml`.

## Testing

- pytest with `asyncio_mode = "auto"`.
- **Test builders, not inline setup.** `_item(**overrides)` and the signature
  helpers in `conftest.py` exist for this. Reuse them.
- **Monkeypatch third-party seams, never Kettle code.** Fake `litellm`,
  `kubernetes`, `subprocess` via `monkeypatch.setitem(sys.modules, ...)`. Tests
  assert against the real module under test.
- No live Temporal, no live cluster, no network in tests.
- `_clean_db` is autouse; do not add teardown for the database.

## Don't do

- **Never merge a pull request.** `trust.allowed_tool("merge")` returns `False`
  unconditionally. This is not configurable and never will be.
- **Never open a PR for a rejected or capped run.** `coordinator.handoff_allowed`
  gates it. Do not work around it by calling `open_handoff` unconditionally.
- **Never add a dev bypass, a stub path, or a default secret.** Missing config
  raises. `_require_secret` has no dev mode on purpose.
- **Never weaken a check to make a test pass.** If a gate is wrong, fix the gate
  and say so in the PR.
- **Never log secrets, tokens, or raw webhook payloads.**
- **Never reorder the activity sequence in `workflows.py` without bumping
  `STAGE_SEQUENCE_VERSION`** and adding a `workflow.version()` patch. Temporal
  replays in-flight workflows against new code.
- **Never call an LLM from `workflows.py`, `coordinator.py`, or any pure module.**
- **Never put stage policy in `api.py`.**
- **Never edit `store.py` schema without a migration.** There is no migration
  tool wired up yet; if you add a column, say so loudly in the PR.

## Documentation

- `docs/architecture.md` — pipeline, façade, adopted upstream patterns
- `docs/pr-review-checklist.md` — the review checklist. It is the source of
  truth for the `review` agent's contract in `factory/kettle.yaml`. Change one,
  change the other.
- `factory/kettle.yaml` — agent contracts

## Before you open a pull request

Run the six commands in **Commands**. Then confirm:

- [ ] Tests pass and coverage did not drop
- [ ] `ruff check` and `ruff format --check` clean
- [ ] `mypy src` clean
- [ ] `factory_check` passes
- [ ] New behavior has a test; changed behavior has a changed test
- [ ] No secrets, no debug prints, no commented-out code
- [ ] If you touched `factory/kettle.yaml`, you also read
      `docs/pr-review-checklist.md` and checked it still applies