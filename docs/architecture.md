# Architecture (P0 slice)

`sources -> ingress (FastAPI) -> automations -> coordinator workflow (Temporal) -> stage Jobs (K8s/Docker) -> PR handoff -> scorers`

- **Determinism:** workflows (`workflows.py`) do no I/O, no clocks, no randomness. All side effects in `activities.py`. Follows `temporal-developer` skill rules.
- **Trust:** intake stamps `trusted`. Unattended GitHub runs (`factory` label) get draft-PR/comment/close only; no shared-config writes; never merge. Mirrors eve-style `trust.ts` policy in `integrations.py` + `automations.py`.
- **Review independence:** review model vendor must differ from implement (`factory_check.py` enforces). Max 2 revision cycles, then park on human.
- **Execution:** `runners.py` builds K8s Job specs (prod) or Docker runs (dev). Worker launches them in activities; logs/costs flow to run records.
- **Improve loop:** `scorers.py` (`tests-pass`, `criteria-met`) groups failures; follow-up work items propose fixes to app or `factory/` definition via PR.

Capacity sketch: 1 work item ~= 4-6 LLM calls + 1-3 Job runs (~2 CPU/4Gi/30m each). Queue depth scales workers (KEDA on Temporal queue in prod).
