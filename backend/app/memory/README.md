# memory

**Status:** implemented (in-memory dev/test adapters; Postgres repositories pending).
Tenant-scoped namespaced memory (short-term/episodic/semantic) with TTL, provenance,
and forget semantics; AGRL append-only hash-chained event ledger with fold-derived
projections (goals, resources, constraints, plans, outcome summary, what-next).
One ledger for goals and resources (AGMS/AGRL consolidation).

## Boundary
Tenant-scoped agent memory: namespaced key/value + episodic records with TTL and lifecycle rules. Subpackage `agrl/` is the Adaptive Goal & Resource Ledger — the event-sourced goal/resource state substrate (see ARCHITECTURE.md §AGRL).

## Owned tables
`memory_namespaces`, `memory_items`; `agrl/` owns `agrl_events` (append-only, hash-chained), `agrl_projections`, `goals`, `goal_links`, `resource_allocations`.

## Public interfaces
`MemoryStore` (put/get/forget with tenancy); `AGRLLedger.append/get_projection` (from `core/contracts.py`); projection builders for goals/resources/plans/outcomes.

## Consumes
`core` only. The ledger is written by all domains; projections are read by all.

## Non-goals
Vector retrieval (see `knowledge/`); the decision logic that consumes goals (callers).

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
