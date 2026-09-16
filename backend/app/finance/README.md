# finance — FOUNDATIONAL operational finance (NOT an accounting system)

**Status:** implemented (MVP). In-memory repository backs tests; Postgres
persistence is modeled (`models.py` + Alembic migration) and lands when the
core builder's `core/db` session factory exists.

> **Disclaimer (binding):** this package is an operational finance cache —
> invoices, payments, expenses, accounts, and money-movement records for
> day-to-day business operations. It is NOT a regulated accounting system.
> **Financial truth lives in NEXORA ERP**, reached through the `NexoraSeam`
> interface (`nexora_seam.py`). Do not use these records for tax filing,
> statutory reporting, or audited financial statements without qualified
> accounting review.

## Boundary
- Invoice lifecycle: `draft → issued → paid`, `issued → overdue → paid`,
  `draft → cancelled`, `issued/overdue → voided`. Issued records are
  immutable — update only in `draft`; corrections via void/reissue or
  reversing transactions.
- Payments applied to invoices (idempotent); no overpayments (raises).
- Expenses, chart of accounts, append-only transactions (no update/delete).
- Reports: AR aging (30-day buckets), cash-flow summary (cash basis) — both
  carry "not audited financials" disclaimers.
- `scan_anomalies` — rule-based heuristics (outlier/duplicate/round-amount),
  flagged only, never blocking; NOT fraud detection.
- Collections: overdue candidates → `generate_collection_reminder` drafts a
  reminder and parks it as `approval_pending` (never sends itself).
- `NexoraSeam` (`nexora_seam.py`) — the integration contract with NEXORA ERP.
  `DisabledNexoraSeam` is the honest default: sync intent recorded,
  `synced=False`, local record stays authoritative-operational.

## What this package explicitly is NOT (never rebuild NEXORA ERP)
No double-entry journal posting, no revenue recognition, no bank
reconciliation, no multi-book accounting, no tax/regulatory logic. The
Postgres adapter for `NexoraSeam` is future work; until it exists, `nexora_ref`
stays null and sync status reports honestly.

## Owned tables
`finance_customers`, `invoices`, `payments`, `finance_expenses`,
`finance_accounts`, `finance_transactions`, `collection_reminders`.

## Public interfaces
- `FinanceService` — all mutations above.
- `ar_aging` / `cashflow_summary` — pure reporting.
- `scan_anomalies` — pure heuristic flagging.
- HTTP: `app/api/routers/finance.py` (`/finance/*`) — thin; the service is the
  boundary. `Idempotency-Key` header is honored on payment recording.

## Consumes
`core` (contracts only). Emits: `finance.invoice.created/issued/overdue/`
`cancelled/voided`, `finance.payment.recorded`, `finance.expense.recorded/`
`deleted`, `finance.transaction.recorded`,
`finance.collections.reminder.prepared`. Reminder *sending* goes through
`comms/` after human approval.

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols or a
   package's public interface.
2. `tenant_id` on every row; every query filters by it.
3. Consequential writes (issue/void/cancel invoice, record payment, delete
   expense, record transaction, collections) require policy evaluation BEFORE
   execution and fail closed when the engine is absent.
4. Never implement ERP accounting logic here — propose a `NexoraSeam` method
   and an ADR instead.
