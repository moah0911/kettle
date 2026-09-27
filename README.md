# Kettle — autonomous software development factory

Self-hosted team of agents that turns requests into reviewed pull requests.

One coordinator conversation per work item. Four stage agents own part of the lifecycle:

- **triage** — research, reproduce, scope + complexity
- **spec** — plan with product behavior, constraints, validation criteria
- **implement** — code change on a branch + tests + evidence, opens PR
- **review** — independent check (different model vendor), advisory verdict, max 2 revision cycles

Flow: `intake -> coordinator -> triage -> plan? -> planning -> approval -> building -> reviewing -> revision? -> handoff -> complete|cancelled`.
Stages are skipped when context already suffices; review can send work back to building. Humans approve specs, answer questions, and merge — agents never merge.

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

Submit work:

```bash
curl -X POST localhost:8000/v1/work-items \
  -H 'content-type: application/json' \
  -d '{"source":"api","title":"Fix login redirect","body":"...","repo":"acme/app"}'
```

## Layout

```
factory/kettle.yaml      factory-as-code (agents, automations, runners, scorers, skills, webhooks)
src/kettle/              models, coordinator routing, automations, API, Temporal workflows/activities, runners, providers, integrations, scorers, MCP
deploy/                  docker-compose + K8s manifests + Helm starter
tests/                   pytest suite (no live Temporal/K8s required)
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

See `docs/` for architecture, intake sources (GitHub/Slack/Linear/Jira/webhooks/API/MCP/cron), trust model, and scorer loop.
