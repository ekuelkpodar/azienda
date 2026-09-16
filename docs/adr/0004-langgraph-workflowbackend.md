# ADR-004: LangGraph for agent loops; WorkflowBackend interface for macro-flows

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.4/§5

## Context

Agent work needs durable, resumable, human-gateable execution without operating a workflow
cluster at MVP scale. The 2026 consensus pattern: agent frameworks own the reasoning loop;
Temporal-class systems own cross-framework macro-orchestration (LIKELY — community consensus).

## Decision

- **LangGraph 1.x** for agent loops, Postgres-backed checkpointer, **interrupts for HITL
  approvals** (approval gates are a first-class graph primitive, not bolted on).
- Macro-workflows behind the `WorkflowBackend` interface (`core/contracts.py`) — the same
  shape `agent-control-plane` already defines. **Temporal is the documented future binding,
  not a day-one dependency.**
- Business logic lives in plain functions; graph nodes stay thin.

## Consequences

- **+** Crash recovery + approval gates without maintaining a workflow engine.
- **+** Explicit graphs are auditable (vs. role-play framework magic).
- **−** LangGraph 1.0 was in alpha at decision time (official release target late Oct 2026 —
  CONFIRMED from the LangChain announcement): pin the exact version in `uv.lock`; keep graph
  code thin so a forced framework change is contained behind the interface.
- **−** Framework-shaped graph code (mitigated by the thin-node rule).

## Step-up trigger

Cross-framework durable workflows, or human waits measured in days at real volume → bind
`WorkflowBackend` to Temporal. The interface makes this a config change, not a rewrite.
