# AI_ARCHITECTURE.md — How AI fits into Azienda

> Spine (from ARCHITECTURE.md): Business Apps → Agents → Control Plane →
> Governance/Security Rail → Data/Knowledge → Integrations. AGRL/AGMS concepts:
> AGRL = the shared event-sourced state substrate; AGMS = the decision layer.
> Labels: CONFIRMED = in code/tests 2026-09-16; otherwise stated.

## Layer map

1. **Business apps** (CRM, tasks, workflows, support, …) — the tools agents use.
   Every write is tenant-scoped and policy-gated. CONFIRMED.
2. **Agents** (`backend/app/agents/`) — registry, task API, planner, router,
   tool registry, scoped action grants. CONFIRMED: registry + task API exist;
   **gap:** the agent service graph is not wired by the production app factory
   (see package README).
3. **Agent Control Plane patterns** — borrowed from Ekue's
   `agent-control-plane` repo (orchestrator, planner, router, memory, policy
   engine, approvals, durable workflow runner, OTel observability, hash-chained
   audit, cost manager). **Not reimplemented** — Azienda reuses the patterns
   behind its own contracts (AGENTS.md §6).
4. **Governance/Security rail** — policy engine, approvals, hash-chained audit,
   budgets + kill switch. CONFIRMED. See GOVERNANCE.md.
5. **Data/Knowledge** — Postgres (+ pgvector) system of record; AGRL
   (event-sourced goal/resource ledger, `backend/app/memory/agrl/`); knowledge
   store with hybrid search. **Gaps (CONFIRMED):** AGRL and knowledge stores are
   in-memory adapters in the running app despite migration tables; pgvector
   live behavior unverified.
6. **Integrations** — MCP (untrusted, rail-gated; ADR 0006), comms providers,
   billing providers. **Gaps (CONFIRMED):** providers not integrated; comms run
   in `log_only` mode.

## The agent loop (the product)

**GOAL → CONTEXT → PLAN → POLICY CHECK → TOOL SELECTION → EXECUTION →
OBSERVATION → FEEDBACK → LEARNING**

- **GOAL** comes from AGRL (adaptive goals) or a human-created task.
- **CONTEXT** comes from the knowledge store + CRM + AGRL projections.
- **POLICY CHECK** happens before every tool call — fail closed.
- **EXECUTION** is metered (cost recorded per model/tool call; AGENTS.md §4).
- **OBSERVATION/FEEDBACK** land in the audit ledger and AGRL outcome projections.
- **LEARNING** is future work (EVALUATION.md).

## Model providers (design, ASSUMED until integrated)

Per `docs/adr/0005-litellm-modelprovider.md`: LiteLLM as the provider abstraction
so model routing (cheap model for routine, frontier for reasoning-heavy) can be
expressed as policy + cost data. **Live providers not integrated (CONFIRMED).**
All demos run deterministic (no LLM) by design.

## What is explicitly NOT AI here

Lead scoring is rule-based and transparent (score breakdown per factor).
The exec briefing is aggregation + rules, not summarization. The support draft
assistant in demo 2 is a template labeled as such. No step in any demo calls a
model.
