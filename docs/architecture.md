# Architecture (reuse-first: adapter-first + plain Jobs + ported stages)

`sources -> ingress (FastAPI) -> automations -> coordinator workflow (Temporal) -> stage Jobs (K8s/Docker) -> PR handoff -> scorers`

Kept façade (stable): `models.WorkItem/Stage/RunRecord`, API routes, `factory/kettle.yaml`, `coordinator.*` signatures, `build_k8s_job`, `ChatRequest`.

Adopted upstream patterns via adapters (no full forks):

- **Gateway/worker (`kron-k8s-agent`):** stateless FastAPI dispatch (`POST /v1/work-items/:id/dispatch` returns workflow ID, starts when `TEMPORAL_HOST` set), workflow-pure + activities-side-effects split, `deploy/k8s` tier + KEDA-on-queue-depth + `CronJob` schedules, `store.WorkItemStore` persistence seam with idempotency keys (Postgres-ready).
- **Stages/trust (`eve`/`Foreman`):** `coordinator.classify` (type/priority/complexity/actionable), `check_definition_of_ready` gate with question-signal park, `select_harness` cross-vendor review, `artifacts` by ID (validated, size-bounded, never-overwrite), `trust.allowed_tool` (merge absent, unattended allowlist).
- **Gates/review (`ai-sdlc`):** DoR before dispatch, reviewer-on-exact-SHA mental model, cost-aware `compare_benchmarks`, `validate_repo_url` brokered git, skill injection env (`runners.skill_env`).
- **Lifecycle (`kelos`/`kube-foundry` patterns, plain Jobs only):** Job spec + skill/MCP env, harness registry (`shell|claude-code|codex|opencode`), draft-PR handoff, `GET /v1/runs/:id/logs`, `GET /v1/dashboard` (by-stage, runs, cost).
- **Improve (`snowl`/EvoSkill):** `score_tests_pass/criteria_met`, `compare_benchmarks`, `update_frontier` top-N, `propose_followup` → new work item (human merges).

Capacity sketch: 1 work item ~= 4-6 LLM calls + 1-3 Job runs (~2 CPU/4Gi/30m each). Queue depth scales workers (KEDA on Temporal queue in prod).
