# ADR-009: Widen `core/contracts.py` protocols to cover API/admin reads

**Status:** accepted · **Date:** 2026-09-15 · **Source:** foundation builder implementation pass

## Context

`core/contracts.py` is the only legal cross-package seam (AGENTS.md §1.1). The
original protocol surface covered the *write/govern* path well (policy evaluation,
approval decision, budget reservation, audit append, credit draw) but had no
operations for the *read/admin* path that the API layer needs:

- Routers needed to list/get approvals, audit entries, budgets, cost-ledger rows,
  spend alerts — and were forced to either import implementation modules directly
  (seam violation) or receive untyped `Any` services.
- `ApprovalStore.request()` did not receive the original `ActionRequest`, the risk
  score, or the risk factors — so an approver deciding in the admin UI could not see
  *why* the action was gated.
- `BudgetDecision` had no `reservation_id`, so `settle()` could not reference the
  reservation `reserve()` created.
- Governance needed a legal way to draw from the tenant's credit pool when recording
  model-call cost; no cross-package seam existed for that.

## Decision

Widen the contracts (all changes in `backend/app/core/contracts.py`):

1. `Approval` gains optional `risk_score`, `risk_factors`, `reasons`;
   `ApprovalStore.request()` now takes the original `ActionRequest`, `risk_score`
   and `risk_factors` and persists them on the record.
2. `AuditEntry` gains `seq` (the hash-chain position).
3. New read views: `BudgetView`, `CostLedgerView`, `SpendAlertView` (frozen
   dataclasses; routers and the CLI consume these, never ORM rows).
4. `ApprovalStore` gains `list_pending() -> tuple[list[Approval], int]`,
   `escalate()`, `sweep_expired()`.
5. `AuditLedger` gains `list_entries()`, `get_by_seq()`.
6. `BudgetEnforcer` gains `create_budget() -> BudgetView`, `list_budgets()`,
   `kill_switch_release()`, `kill_switch_status()`, `cost_ledger()`,
   `spend_alerts()`.
7. New `CreditLedger` protocol (`draw(tenant, credits, reason, task_id)`) — the
   legal governance→billing seam. `BillingService` implements it; the cost recorder
   draws credits through it instead of reaching into billing internals.
8. `BudgetDecision` gains `reservation_id`.

Implementations (`ApprovalStoreImpl`, `AuditLedgerImpl`, `BudgetEnforcerImpl`)
were converted to return contract dataclasses. Routers depend only on the
protocols via `app/api/deps.py` providers. `app/api/deps.py` and the operator CLI
remain the documented composition-root exception: the one place allowed to import
concrete implementations and bind them to protocols.

## Consequences

- **+** The seam stays the seam: no router imports another package's internals;
  `mypy --strict` passes on all 43 foundation source files.
- **+** Admin UI and CLI read paths are typed end-to-end.
- **−** Protocol surface is larger; every future implementation of these protocols
  (including test fakes) must satisfy the wider interface. The repo rule stands:
  widen a contract only with all implementations + tests updated in the same change.
- **−** `ApprovalStore.list_pending()` returns `(items, total)`; consumers that
  assumed a bare list (e.g. command-center's `_pending_approvals`) must unpack the
  tuple. A tolerance shim is in place; the canonical shape is the tuple.

## Step-up trigger

If a third consumer needs operations outside these protocols, split reads into a
dedicated query interface rather than widening the command protocols further
(CQRS-lite); revisit when the admin surface stabilizes.
