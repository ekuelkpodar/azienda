# workflows

**Status:** implemented (DSL, service, re-entrant runner, actions, conditions,
seeded lead workflow, router, migration, tests).

## Boundary

Versioned workflow definitions (DAGs), immutable published versions,
executions, and a durable event log per execution. The runner is re-entrant:
`drive()` advances an execution as far as possible and is safe to call
repeatedly; approval nodes park until `signal()` delivers a decision.

## Owned tables (CONFIRMED — in migration `0004_crm_tasks_workflows`)

`workflow_definitions`, `workflow_versions`, `workflow_executions`,
`execution_events`.

## Public interfaces

- `WorkflowService` (`app/workflows/service.py`): definition CRUD,
  `publish_version` (immutable snapshot), `start_execution` (idempotent),
  `signal_execution`, `cancel_execution`, `inspect`, event listing.
- `WorkflowRunner` (`app/workflows/runner.py`): `drive(execution_id)`,
  `signal(...)`, `cancel(...)`. All cross-package effects go through injected
  protocol ports (`LeadPort`, `TaskPort`, `NotificationPort`,
  `AgentTaskDelegate`, `ToolExecutor`, `ApprovalStore`) — the package never
  imports CRM/tasks internals (CONFIRMED).
- `app/workflows/dsl.py`: `NodeSpec`/`EdgeSpec`/`DagSpec` + `validate_dag`
  (exactly one trigger; per-type required config; edge endpoints exist;
  reachability from trigger; acyclicity; `on_error` targets exist).
- `app/workflows/conditions.py`: safe expression evaluator — no `eval`
  (`and`/`or`/`not`, comparison ops, `{var: path}` resolution; unknown shapes
  and operators raise `ExpressionError`) (CONFIRMED).
- `app/workflows/actions.py`: action catalog — `research_stub`,
  `crm.lead.set_status`, `crm.lead.rescore`, `crm.activity.log`,
  `task.create`, `outreach.draft`. Every action declares the port it needs;
  missing ports raise `ActionError` (fail closed, never silent).
- `app/workflows/ports.py`: the protocol definitions (the seam).
- `app/workflows/definitions/lead_outreach.py`: the seeded
  **`lead-qualification-outreach`** workflow (see below).
- Router: `app/api/routers/workflows.py` (prefix `/api/v1/workflows`).

## Node types

`trigger → condition → agent → tool → approval → action → notification`.
Retries are bounded (`retry.max_attempts` ≤ 10, `backoff_seconds`); exhausted
retries follow `on_error` when set, else the execution fails with a recorded
error. Approval nodes evaluate policy first: ALLOW skips, DENY follows the
`denied` edge (or fails closed), REQUIRE_APPROVAL parks the execution
(`waiting_approval`, `approval.requested` event) until `signal()`.

## Lead workflow: `lead-qualification-outreach` (CONFIRMED, 12 nodes / 14 edges)

```
new_lead → qualify ──true──→ research (explicit stub) → rescore → score_gate
             │                                              ├──true──→ draft → approve_send ──approved──→ log_send → followup task → notify
             │                                              │                          └──denied──→ manual_review task → notify
             │                                              └──false──→ nurture_low task → notify
             └──false──→ nurture_low task → notify
```

- `qualify`: `lead.status == "new"` AND `lead.source != "purchased_list"`.
- `score_gate`: rule-based score ≥ 40 (default `SCORE_THRESHOLD`).
- `research` is an **explicit stub**: it records what *would* be researched and
  states that no external provider is configured. It never claims external data.
- `approve_send` uses policy ref `outreach.send.v1`; approval is human unless
  policy allows outright.
- "Send" means: after approval, an `outreach_sent` activity is **logged** to
  the CRM timeline. No external delivery is claimed or performed — actual
  delivery is owned by `comms/` (ASSUMED; confirm with comms builder).
- Seeded idempotently via `POST /definitions/seed/lead-outreach` or
  `WorkflowService.seed_lead_outreach(tenant)`.

## Key behaviors (CONFIRMED by `tests/test_workflows.py`, 25 tests)

- DAG validation rejects cycles, missing/duplicate triggers, missing required
  node config, unreachable nodes, dangling edges.
- Published versions are immutable (draft edits + republish create a new
  version; v1 bytes never change).
- Execution start is idempotent (same key → same execution).
- Policy-before-start; tenant isolation on definitions/versions/executions.
- Full event trail per execution (`execution.started`, `node.started`,
  `node.completed`, `node.retry`, `node.failed`, `approval.requested`,
  `approval.decided`, `execution.finished`, …).
- Approval: park → approve → resume; deny → `denied` edge; cancel a parked
  execution; signaling an unknown approval errors.
- All three lead-workflow branches tested end-to-end (approved / low-score /
  denied), including task creation and activity logging.

## Consumes

`core/contracts.py` protocols only, plus the injected ports. The concrete
`LeadPort`/`TaskPort` adapters live in the API composition layer
(`app/api/routers/_common.py`), NOT in this package (CONFIRMED).

## Gaps / honest notes

- **No tool executor is wired** (`ToolExecutor` is `None` in the current
  composition): `tool` nodes fail closed with "tool executor is not
  configured". Tool-using workflows need the tools builder's executor injected.
- **No notification port is wired**: `notification` nodes record event-only
  delivery ("comms not wired"). Real delivery needs `comms/`.
- **Agent nodes** delegate to the injected `AgentTaskDelegate`; the stub used
  in tests is not production execution (the ACP builder owns real runs).
- Execution is **synchronous drive** (no background worker/queue yet);
  long-running workflows will need the ARQ worker integration (ASSUMED
  roadmap).
- Two `WorkflowError` classes exist (`service.py` and `runner.py`) — same
  name, different types. Harmless today (both caught at the router), but
  should be unified (KNOWN wart).
- The runner loads the lead snapshot via `LeadPort` at drive start; very
  long-lived executions could act on a stale snapshot (LIKELY acceptable for
  MVP; re-load or version-stamp before prod).
