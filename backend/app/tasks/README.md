# tasks

**Status:** implemented (service, models, schemas, state machine, router, migration, tests).

## Boundary

Projects, milestones, tasks/subtasks, dependencies, assignments, comments,
delegation, and bulk operations. Work-execution layer. Every row is
tenant-scoped; transitions are policy-gated and recorded; bulk operations are
durable and replayable.

## Owned tables (CONFIRMED — in migration `0004_crm_tasks_workflows`)

`projects`, `milestones`, `tasks`, `task_dependencies`, `task_transitions`,
`task_comments`, `task_bulk_op_records`.

## Public interfaces

- `TaskService` (`app/tasks/service.py`): project/milestone/task CRUD,
  `transition_task`, `assign_task`, `delegate_task`, `add_dependency`,
  comments, `bulk`, outcome inspection, transition history.
- `can_transition(from, to)` (`app/tasks/service.py`): pure function encoding
  the binding state machine — the single source of truth for legal moves
  (CONFIRMED; unit-tested directly).
- `app/tasks/schemas.py`: Pydantic v2 request/response schemas.
- Router: `app/api/routers/tasks.py` (prefix `/api/v1/tasks`; note
  `/{task_id}` is declared after `/bulk` so the literal route wins —
  CONFIRMED).
- Domain events (via injected `EventBus`): `tasks.task.created`,
  `tasks.task.transitioned`, `tasks.task.assigned`, `tasks.task.delegated`,
  `tasks.dependency.added`, … (see service `_emit` calls — CONFIRMED).

## State machine (CONFIRMED)

```
pending -> planning -> waiting_approval -> executing -> completed
    |           |             |                  |--> blocked --^
    v           v             v                  v
cancelled   cancelled     failed/cancelled   failed/cancelled
```

- `completed`, `failed`, `cancelled` are terminal; illegal moves raise
  `IllegalTransitionError` (HTTP 409 `illegal_transition`).
- A task cannot complete while its dependencies are incomplete
  (`TaskValidationError`, tested).
- Dependency edges are cycle-checked at insert (`DependencyCycleError`,
  including self-dependency).
- Every transition appends a `task_transitions` row (from/to/actor/note).

## Key behaviors (CONFIRMED by `tests/test_tasks.py`, 20 tests)

- **Tenant isolation** at service and HTTP level (404 `not_found` envelope
  across tenants).
- **Policy-before-write** on create/transition/assign/delegate/bulk.
- **Idempotency:** all mutating HTTP routes require `Idempotency-Key`
  (422 `missing_idempotency_key` when absent); task creation also accepts a
  per-row `idempotency_key` for safe retries.
- **Bulk operations** (`POST /tasks/bulk`): create/update/transition/assign
  in one call; each op carries its own idempotency key persisted in
  `task_bulk_op_records`, so replaying a batch returns stored results without
  re-executing (per-op `ok`/`replayed`/`error` results; duplicate keys inside
  one batch are rejected).
- **Delegation** (`delegate_task`) is the controlled entry point to the agent
  workforce: policy-gated, records delegation intent on `task.plan`, moves
  `pending -> planning`. The agents package owns actual agent-run creation
  (ASSUMED — this package only records the handoff).

## Consumes

`core/contracts.py` protocols only. Never imports sibling packages (CONFIRMED).

## Gaps / honest notes

- **Concurrent duplicate bulk batches** can both execute before the
  uniqueness race is detected; replay is durable but exactly-once under true
  concurrency is UNPROVEN — needs a DB-level advisory lock or serializable
  guard before high-volume use.
- Task **reminders/escalations** on overdue tasks are not implemented
  (ASSUMED owned by `scheduling/`; confirm).
- Time tracking / estimates exist as columns (LIKELY) but no rollup or
  reporting logic is implemented.
- `delegate_task` records intent; end-to-end agent execution is owned by the
  ACP builder (their 67/67 tests cover the sandbox side — CONFIRMED per
  coordination log).
