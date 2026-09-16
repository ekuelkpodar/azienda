# agents

**Status:** implemented (in-memory dev/test adapters; Postgres repositories pending).
Registry with the 10 seeded workforce agents, versioning, and pause/resume lifecycle;
rule-based planner (inspectable plans); multi-factor router (capability × authorization ×
cost × risk); policy-gated tool executor with scoped grants, schema validation, and
idempotency; model registry + LiteLLM provider with labeled stub fallback; MCP manager
(untrusted lifecycle, policy-before-invocation); durable orchestrator
(plan → budget reserve → policy gate → execute → AGRL outcome); eval framework with
regression suite. Production Postgres repositories are not yet bound.

## Boundary
The AI agent workforce: agent registry + versioning, orchestrator, planner, router (capability × cost × risk × policy), tool registry/executor, model registry (LiteLLM behind `ModelProvider`), MCP client management. Subpackages: `registry`, `orchestrator`, `planner`, `router`, `tools`, `models`, `mcp`.

## Owned tables
`agents`, `agent_versions`, `agent_runs`, `tool_registry`, `tool_calls`, `model_registry`, `mcp_servers`.

## Public interfaces
Implements `ToolExecutor`, `ModelProvider`, `ToolRegistry`, `AgentRegistry`; `Orchestrator.run_task(task_id)` — the governed task pipeline; events: `agent.run.started/finished`, `tool.called`, `model.used`.

## Consumes
`core`, `governance` (EVERY tool call is policy-gated — no bypass), `knowledge` (retrieval), `memory` (context).

## Non-goals
Business apps (callers, not owners); the policy engine itself (see `governance/`).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols (or this package's
   own public interface). No cross-package private imports — CI enforces this with import-lint.
2. Every row this package writes carries `tenant_id`. Every query filters by it. Add a
   cross-tenant invisibility test for every new table.
3. Mutations that matter emit AGRL/domain events via the `EventBus`; the Postgres ledger
   is truth, the bus is transport.
4. Anything irreversible, financial, bulk, or externally visible goes through the
   governance rail (`governance/`) BEFORE execution. Fail closed.
5. No secrets in code or logs. Use the `SecretBroker` interface; config comes from env.
