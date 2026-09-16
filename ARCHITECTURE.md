# Azienda — Architecture

**Status:** normative for the build. **Date:** 2026-09-15.
**Evidence labels** (Ekue's rule) mark factual claims: **CONFIRMED** (verified from a
primary source), **LIKELY** (strong secondary evidence), **ASSUMED** (explicit working
assumption), **UNKNOWN** (unverified — do not act on as fact).
Research basis: `docs/research/competitive-analysis.md`, `docs/research/stack-decision.md`,
`docs/research/commercialization.md`, and the 8 adopted ADRs in `docs/adr/`.

## 1. What this is

Azienda is a commercial **AI Business OS**: modular business applications (CRM, tasks,
workflows, marketing, support, scheduling, finance, comms) operated by an **AI agent
workforce**, managed by an **Agent Control Plane**, constrained by a **governance/security
rail**, grounded in a **data/knowledge layer**, and connected through **integrations**.

The product thesis (competitive-analysis §5, CONFIRMED market evidence): **nobody owns the
governed closed loop**. Every competitor is an assistant that answers, an executor trapped
in one department, a pipe-builder with no business semantics, or a governance layer sold
only to the enterprise. Azienda is the layer that runs the full loop across departments.

## 2. The core principle: the AI-native operating loop

Everything in this architecture exists to run one loop, durably and under policy:

```
GOAL → CONTEXT → PLAN → POLICY CHECK → TOOL SELECTION → EXECUTION
     → OBSERVATION → FEEDBACK → LEARNING → (GOAL …)
```

Design consequences, each binding on builders:

1. **GOAL is persistent, not session-bound.** Goals, constraints, and resource allocations
   live in the AGRL event ledger (§9) and survive sessions, agents, and departments.
   A chat that ends does not end the goal.
2. **POLICY CHECK sits in the execution path, before the tool call.** Not advisory, not a
   quarterly review. Every consequential action is evaluated allow/deny/require_approval
   with risk scoring, least-privilege tool grants, and human approval gates keyed to
   risk × reversibility × financial impact × data sensitivity (ADR-008, `governance/`).
3. **TOOL SELECTION is provider-neutral.** MCP commoditizes tool access (competitive
   research: ~31k MCP servers, Sept 2026 — LIKELY). Value moves to *which tool, under which
   policy, at what cost, toward which goal* — decided by the router + policy engine.
4. **EXECUTION is durable.** Long-running business processes (collections, onboarding,
   fulfillment) run on durable semantics with checkpoints and compensation/rollback,
   via the `WorkflowBackend` interface (LangGraph now, Temporal documented — ADR-004).
5. **OBSERVATION → FEEDBACK → LEARNING closes across the business.** Every outcome lands
   on the outcome ledger with fully-loaded cost (tokens, retries, human-review time —
   InfoWorld reports all-in agent cost at 2–5× raw token cost — LIKELY), feeding per-workflow
   eval suites, cost-per-outcome trends, and policy refinement. History compounds; it is not discarded.
6. **Progressive autonomy L0–L5** is a per-workflow dial, not a global switch. Autonomy level
   is stored on the agent version and the workflow definition; the governance rail enforces it.

## 3. Architecture spine

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Business Applications                                    │
│    CRM · Tasks · Workflows · Marketing · Support ·          │
│    Scheduling · Finance · Comms · Command Center            │
└──────────────────────────────┬──────────────────────────────┘
                               │ intents (goals + constraints)
┌──────────────────────────────▼──────────────────────────────┐
│ 2. AI Agents (workforce)                                    │
│    registry · orchestrator · planner · router · tools ·      │
│    models (LiteLLM) · MCP clients                           │
└──────────────────────────────┬──────────────────────────────┘
                               │ governed action requests
┌──────────────────────────────▼──────────────────────────────┐
│ 3. Agent Control Plane                                      │
│    task admission · routing · durable workflow runner ·     │
│    agent lifecycle · model registry                         │
│    (patterns reused from github.com/ekuelkpodar/            │
│     agent-control-plane — NOT reimplemented)                │
└──────────────────────────────┬──────────────────────────────┘
                               │ allow / deny / require_approval
┌──────────────────────────────▼──────────────────────────────┐
│ 4. Governance / Security Rail  ◄── spans 2, 3, 5, 6        │
│    policy engine · risk scoring · approvals/HITL · audit    │
│    ledger (hash-chained) · budgets + kill switches ·        │
│    secret broker · identity                                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ 5. Data / Knowledge                                         │
│    Postgres 16/17 + pgvector (single system of record) ·    │
│    AGRL event ledger + projections · memory · RAG           │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ 6. Integrations                                             │
│    MCP servers · SaaS APIs (NEXORA ERP, GHL, …) · browsers · │
│    S3-compatible object storage · comms providers           │
└─────────────────────────────────────────────────────────────┘
```

**Seam rules (binding):**
- `github.com/ekuelkpodar/agent-control-plane` — we reuse its **patterns** (admission chain,
  policy-before-execution, scoped credentials, `WorkflowBackend` interface, hash-chained audit)
  and consume it as a library/service boundary where it fits. We do **not** fork or rebuild it.
- `github.com/ekuelkpodar/nexora-erp` — finance/inventory/order **truth** lives there. Azienda's
  `finance/` module is foundational with an explicit `NexoraSeam`; consequential financial actions
  route through NEXORA's approval path. We do **not** rebuild an ERP.
- MCP servers are **untrusted tool providers** (ADR-006). Every MCP invocation passes the
  governance rail; no ambient credentials; outputs treated as untrusted data.

## 4. Package map (modular monolith)

`backend/app/` — one deployable, strict boundaries. Cross-package imports go **only** through
`core/contracts.py` protocols (CI enforces with import-lint). Each package documents its boundary,
owned tables, and public interfaces in its own `README.md`.

| Package | Owns | Key contract |
|---|---|---|
| `core/` | config, DB, security, events, tenancy, **all contracts** | `contracts.py` (source of truth) |
| `governance/` | policy, risk, approvals, audit, budgets | `PolicyEngine`, `ApprovalStore`, `AuditLedger`, `BudgetEnforcer` |
| `crm/` | orgs, contacts, leads, opportunities, pipelines, activities | `CRMService` |
| `tasks/` | projects, tasks, state machine | `TaskService`, `can_transition` |
| `workflows/` | definitions, executions | `WorkflowBackend` |
| `agents/` | registry, orchestrator, planner, router, tools, models, mcp | `Orchestrator.run_task`, `ToolExecutor`, `ModelProvider` |
| `memory/` (+`agrl/`) | memory namespaces; **AGRL event ledger + projections** | `MemoryStore`, `AGRLLedger` |
| `knowledge/` | sources, documents, pgvector chunks, edges | `KnowledgeStore` |
| `comms/` | channels, conversations, messages (pass-through billed) | `CommsService.send` |
| `marketing/` | campaigns, audiences, content assets | `MarketingService` |
| `support/` | tickets, SLA, resolution | `SupportService` |
| `scheduling/` | calendars, events, bookings | `SchedulingService` |
| `finance/` | invoices/payments/expenses + **NEXORA seam** | `FinanceService`, `NexoraSeam` |
| `billing/` | plans, subscriptions, credits, metering, invoicing (own SaaS) | `BillingService` |
| `api/` | HTTP routers + schemas only — **no business logic** | routers per `API.md` |
| `cli/` | operator CLI (provision, migrate, ledger verify, seed) | `azienda` command group |

Extraction path: because every boundary is an interface, any package can later become a service
without rewriting callers — but **microservices are explicitly not the MVP** (ADR-001).

## 5. Request lifecycle (synchronous REST)

```
Client → FastAPI (api/) → dependencies: auth → tenant → RBAC → rate limit
  → router (schema validation, Pydantic v2)
  → service interface (domain package)
      → governance rail check (policy evaluate — allow path continues)
      → budget reserve (if cost-bearing)
      → Postgres (tenant-scoped, RLS backstop)
      → AGRL/domain event → EventBus (transport only)
  → response (Pydantic schema) + OTel trace + audit entry
```

Rules: idempotency keys on all mutating POSTs (`Idempotency-Key` header); cursor pagination;
error envelope `{"error": {"code","message","details","trace_id"}}`; every mutation that matters
emits an event; the ledger is truth, the bus is transport.

## 6. Governed task pipeline (the AI-native loop, asynchronous)

This is the lifecycle every agent-executed task follows — the operating loop made concrete:

```
TASK created (PENDING)
  │  AGRL: goal.linked / task.created
  ▼
PLANNING — planner produces plan (steps × tools × est. cost)
  │  AGRL: plan.proposed
  ▼
POLICY CHECK — per-step: PolicyEngine.evaluate → allow | deny | require_approval
  │  deny → FAILED (with reasons, audited) · require_approval → WAITING_APPROVAL
  ▼
WAITING_APPROVAL — human decides (approve / deny / edit); timeout == DENY (fail closed)
  │  AGRL: approval.requested / approval.decided
  ▼
BUDGET RESERVE — BudgetEnforcer.reserve(est. credits); insufficient → BLOCKED + alert
  ▼
EXECUTING — orchestrator runs steps via LangGraph + checkpointer
  │  each TOOL SELECTION → router (capability × cost × risk × policy)
  │  each tool call → policy re-evaluated + scoped grant (no ambient authority)
  │  each model call → ModelProvider (LiteLLM) + CostRecorder
  │  interrupts on approval-needed steps → back to WAITING_APPROVAL
  ▼
OBSERVATION — step results, tool outputs (untrusted), costs → audit ledger + AGRL
  │  failure → retry w/ backoff (bounded) → still failing → BLOCKED (human) or FAILED
  ▼
COMPLETED / FAILED → outcome record: goal, plan, policy decisions, tool calls,
  retries, human interventions, FULLY-LOADED COST → outcome ledger
  │  AGRL: outcome.recorded · resource.released
  ▼
FEEDBACK → LEARNING — eval scores, cost-per-outcome trends, policy refinements
  (async; never blocks the task path)
```

**Task state machine (binding):**
`PENDING → PLANNING → WAITING_APPROVAL → EXECUTING → BLOCKED → COMPLETED | FAILED`,
plus `CANCELLED` reachable from any non-terminal state. All transitions are recorded in
`task_transitions` and mirrored as AGRL events. The diagram `docs/diagrams/task-pipeline.md`
shows the sequence; `docs/diagrams/agent-lifecycle.md` shows the agent-side states.

## 7. Tenancy

- **Hierarchy:** Platform → Tenant → {users, agents, tools, policies, knowledge, budgets, audit}.
- **Primary gate:** `tenant_id` on every row, application-enforced (query filters + tests asserting
  cross-tenant invisibility). Tenant is resolved from the JWT claims — never from a client-supplied
  parameter.
- **Defense in depth:** Postgres Row-Level Security on all tenant tables (ADR-008).
- **Agents as principals:** agents get per-action scoped grants with expiry; zero standing
  credentials (anti-confused-deputy). Secrets are brokered, never passed.

## 8. Security model

1. **Humans:** OIDC-ready login; short-lived JWT access (5–15 min) + rotating refresh with reuse
   detection; HttpOnly Secure cookies for the SPA; RBAC roles per tenant; the policy engine for
   fine-grained action checks.
2. **Agents/tools:** no ambient authority — per-action, just-in-time, scoped, expiring grants.
3. **MCP:** servers are untrusted; rail-gated invocations; schema-validated; outputs untrusted
   (prompt-injection surface → DLP/injection detectors); MCP OAuth is server identity, not user auth.
4. **Secrets:** env locally; `SecretBroker` interface with Vault/Doppler/cloud adapters in prod.
   Never in git, logs, or client responses.
5. **Audit:** append-only hash-chained ledger (`audit_ledger`, `agrl_events`); chain verification
   is a CLI command and a scheduled check.
6. **Network:** single deployable; TLS at the edge; Redis/Postgres not publicly exposed.
7. **Fail closed:** approval timeout = DENY; policy evaluation failure = DENY; budget
   exhaustion = freeze new agent work (kill switch), never silent overspend.

## 9. AGRL — the event-sourced goal/resource substrate

Ekue's AGRL concept (Adaptive Goal & Resource Ledger) is the system's memory of *intent and
capacity*. **One event ledger, not three** — the orchestrator's event store, the audit-adjacent
event history, and AGRL are a single `agrl_events` table (consolidation rule from the mandate).

- **Event ledger** (`agrl_events`): append-only, hash-chained; every goal/resource lifecycle fact:
  `goal.created/updated/reprioritized/suspended/completed`, `resource.registered/allocated/released`,
  `plan.proposed/approved`, `outcome.recorded`, `learning.applied`.
- **State ledger** (projections in `agrl_projections` + `goals`, `resource_allocations` tables):
  current state = fold of the immutable history. Rebuildable at any time.
- **Relationship graph**: `goal_links` (decompose/blocks/depends-on/conflicts-with) — goal conflicts
  are first-class with resolution history.
- **Adaptive memory**: outcomes and feedback feed prioritization weights and policy refinements.

Invariants (binding): projections are **derived, never edited**; the ledger is append-only;
`agent-control-plane` and business apps **consume** AGRL — nothing except the AGRL writer mutates
goals directly (mirrors the ACP principle "AGRL stays above and separable"). See
`docs/diagrams/agrl-flow.md` and `DATABASE.md` § AGRL.

## 10. Data & knowledge

- **Postgres 16/17 + pgvector** is the single system of record (ADR-002): relational ledgers,
  JSONB agent state, full-text + HNSW vector search with tenant filtering in one query.
- **Redis 7** is cache, rate limits, refresh-token denylist, locks, ARQ queue — never the system
  of record (ADR-003). Valkey is the documented license-safe drop-in.
- **S3-compatible object storage** for bulk bytes (uploads, exports, audit snapshots, artifacts).
- **Event bus:** in-process asyncio behind `EventBus`; Redis Streams then NATS JetStream as
  documented step-ups (ADR-007).

## 11. Observability & cost control

- OTel from day one: traces carry decision provenance (`task.id`, `policy.decision`,
  `model.selected`, `cost.usd`); structured JSON logs (structlog); Prometheus/Grafana via
  compose profile.
- **Cost is a first-class signal:** LiteLLM spend tracking + `CostRecorder` + per-tenant budgets
  + kill switches are **P0** (commercialization research: model API cost is the only unbounded
  line item; blended target ≈ $0.05–$0.10/task at 70/25/5 model mix — ASSUMED operating target).
- Outcome ledger records fully-loaded cost per business outcome (tokens × retries × human-review
  time) — this is the pricing-trust moat from the competitive thesis.

## 12. What is explicitly NOT in this architecture (MVP)

Kafka/Redpanda · Temporal cluster · dedicated vector/graph DBs · Next.js/SSR · LiteLLM Proxy ·
K8s · MCP SDK v2 · Langfuse/Datadog day-one · multi-region · schema-per-tenant · custom agent
framework. Each has a documented step-up trigger in its ADR. (ADR-002/004/007.)

## 13. Open questions for builders (do not guess — decide and record)

1. Exact seam with `agent-control-plane`: library import vs. sidecar service for the policy engine
   at MVP — ASSUMED library for MVP; confirm against ACP's packaging.
2. LangGraph 1.x is pre-release (stable target late Oct 2026 — CONFIRMED): pin exact version in
   `uv.lock`; keep graph code thin.
3. Trademark: "Azienda" legal availability is UNKNOWN — attorney + USPTO/EUIPO review required
   before launch (commercialization §1).
