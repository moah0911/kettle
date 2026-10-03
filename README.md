# Kettle — Autonomous Software Development Factory

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Temporal](https://img.shields.io/badge/Temporal-durable_orchestration-39477F?style=flat)](https://temporal.io/)
[![Postgres](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-Jobs-326CE5?style=flat&logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![LiteLLM](https://img.shields.io/badge/LLM-LiteLLM_OpenAI_compatible-6E56CF?style=flat)](https://github.com/BerriAI/litellm)
[![License](https://img.shields.io/badge/License-see_REPO-green?style=flat)](./LICENSE)

Self-hosted team of agents that turns requests into **reviewed pull requests — humans merge, agents never merge.**

> Built by [Boyina Gowtham](https://github.com/moah0911) · AI Engineer × DevRel

One coordinator conversation per work item. Five stage agents own part of the lifecycle:

- **triage** — research, reproduce, scope + complexity
- **story** — user story, acceptance criteria, edge cases, out of scope, open questions
- **spec** — plan with product behavior, constraints, validation criteria
- **implement** — code change on a branch + tests + evidence, opens PR
- **review** — independent check (different model vendor), advisory verdict, max 2 revision cycles

Flow: `intake -> coordinator -> triage? -> story -> approval -> planning -> approval -> building -> reviewing -> revision? -> handoff -> complete|cancelled`.
Stages are skipped when context already suffices; review can send work back to building. Humans approve stories and specs, answer questions, and merge — agents never merge.

Only an **approved** review opens a draft PR. A rejected run, or one that exhausts its two revision cycles, parks at `handoff` with its branch pushed but no PR — a rejection rendered as an ordinary draft is a rejection nobody reads.

The PR body is composed from stored artifacts (`src/kettle/handoff.py`): story, acceptance criteria, and review findings by severity. Read the full artifacts with:

```bash
curl localhost:8000/v1/work-items/<id>/artifacts -H 'x-api-key: ...'
```

Every agent's purpose, boundaries, and output contract live in `factory/kettle.yaml` and are compiled into runtime prompts by `src/kettle/prompts.py`. `kettle check` fails CI if an agent is missing `must_not` or `output_format`.

## Stack

Python 3.11+ · FastAPI · Temporal (durable orchestration) · Postgres · Kubernetes Jobs (Docker fallback for dev) · any OpenAI-compatible LLM via LiteLLM · MCP server for local agents.

## Quickstart (dev, no cluster)

```bash
uv sync --extra dev
uv run pytest --cov=kettle tests/ -q
uv run uvicorn kettle.api:app --reload --port 8000
# Temporal dev server (needs temporal CLI): temporal server start-dev
# Full stack: docker compose up --build
```

Submit work (needs `X-API-Key: $KETTLE_API_KEY` on every call below):

```bash
curl -X POST localhost:8000/v1/work-items \
  -H "x-api-key: $KETTLE_API_KEY" -H 'content-type: application/json' \
  -d '{"source":"api","title":"Fix login redirect","body":"...","repo":"acme/app"}'
```

Webhook intake (GitHub/Slack/Linear/Jira) dispatches the coordinator
automatically when `TEMPORAL_HOST` is configured; the response carries
`dispatched: true|false`. Direct API creates wait for a manual dispatch:

```bash
curl -X POST localhost:8000/v1/work-items/<id>/dispatch \
  -H "x-api-key: $KETTLE_API_KEY"
```

Approve a story or spec — this both records the decision and signals the
running workflow (`signaled: true` means it heard you):

```bash
curl -X POST localhost:8000/v1/approvals \
  -H "x-api-key: $KETTLE_API_KEY" -H 'content-type: application/json' \
  -d '{"work_item_id":"<id>","kind":"story","approved":true}'
```

Provider webhooks expect Kettle's relay envelope, not raw provider payloads —
see [`docs/webhooks.md`](docs/webhooks.md).

## Layout

```
factory/kettle.yaml      factory-as-code (agents, automations, runners, scorers, skills, webhooks)
src/kettle/              models, coordinator routing, prompts (knowledge layer), automations, API, Temporal workflows/activities, runners, providers, integrations, scorers, MCP
deploy/k8s/              K8s manifests (namespace, RBAC, deployments, KEDA, CronJob)
tests/                   pytest suite (no live Temporal/K8s required)
AGENTS.md                project rules for AI coding assistants (tool-agnostic)
docs/                    architecture and the PR review checklist
```

## Factory-as-code

```bash
uv run python -m kettle.factory_check --factory ./factory
```

## Skills installed

- `temporal-developer` (official Temporal durable-execution guide)
- `python-fastapi-development` (FastAPI + SQLAlchemy + Pydantic workflow)
- `github-workflow-automation` (PR/CI automation)
- Local: `python-backend`, `python-testing-patterns`, `pytest-coverage`, `system-design`

<## Working on Kettle

Read [`AGENTS.md`](AGENTS.md) before your first edit. It is tool-agnostic — the
same rules apply to Claude Code, Cursor, Codex, Copilot, opencode, Aider, or a
human with a text editor. Pull requests are reviewed against
[`docs/pr-review-checklist.md`](docs/pr-review-checklist.md), which is also the
source of truth for the `review` agent's contract.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — pipeline, façade, adopted upstream patterns
- [`docs/webhooks.md`](docs/webhooks.md) — intake contract: the relay envelope, per-source shapes, what gets dispatched
- [`docs/pr-review-checklist.md`](docs/pr-review-checklist.md) — the review checklist
- [`AGENTS.md`](AGENTS.md) — project rules, commands, architecture invariants
- `factory/kettle.yaml` — agent contracts

See `docs/` for architecture, intake sources (GitHub/Slack/Linear/Jira/webhooks/API/MCP/cron), trust model, and scorer loop.

## Why Kettle?

Manual triage → spec → code → review doesn't scale. Kettle makes each stage explicit, durable (Temporal), and auditable — with independent cross-vendor review and a max of 2 revision cycles so work always converges to a human decision.

## Contributing

PRs welcome. Run `uv run pytest --cov=kettle tests/ -q` and `uv run python -m kettle.factory_check --factory ./factory` before opening a PR.

## Author

**Boyina Gowtham** — AI Engineer × DevRel · [GitHub](https://github.com/moah0911) · [LinkedIn](https://www.linkedin.com/in/boyinagowtham/) · [Medium](https://thegowtham.medium.com/)
