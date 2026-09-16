# governance

**Status:** implemented — the governance/security rail. Verified 2026-09-15:
269 backend tests pass, `ruff check` and `mypy --strict` clean, Alembic
0001→0003 upgrades clean on SQLite with zero model/migration column diffs.

## Boundary
Every consequential agent or user action is evaluated here BEFORE execution:
policy decision (allow/deny/require_approval), risk scoring, human approvals,
append-only hash-chained audit, budgets and the cost ledger with kill switches.
Fail-closed everywhere: approval timeout means DENY.

## Owned tables
`policies`, `policy_versions`, `risk_scores`, `approvals`, `approval_events`,
`audit_ledger` (hash-chained, DLP redaction runs BEFORE hashing),
`budgets`, `budget_periods`, `budget_reservations`, `cost_ledger`, `spend_alerts`.

## Public interfaces
Implements `PolicyEngine`, `RiskScorer`, `ApprovalStore`, `AuditLedger`,
`BudgetEnforcer`, `CostRecorder` from `core/contracts.py`.
- `governance/policy/engine.py` — `RulePolicyEngine`: structural rule evaluation
  (CONFIRMED real but limited; full OPA/Rego bundle evaluation is future work).
  Rego source is accepted and stored for future OPA use.
- `governance/policy/risk.py` — risk scoring; bands from settings
  (`AZIENDA_RISK_LOW_MAX`/`AZIENDA_RISK_HIGH_MIN`); est. cost above
  `AZIENDA_COST_APPROVAL_THRESHOLD_USD` (ASSUMED default $5.00) forces approval.
- `governance/approvals/store.py` — approval persistence, timeout sweep
  (expired → DENIED), escalation. `GovernanceGate.evaluate_action(...)` is the
  single choke point.
- `governance/audit/ledger.py` — hash-chained append-only ledger; `verify_chain()`;
  no mutation API by design.
- `governance/budgets/` — budgets, reservations, settlement, alerts, tenant kill
  switch (P0: unbounded agent cost is the #1 business risk). Cost recording draws
  credits via the `CreditLedger` contract (ADR-009), never billing internals.
- `governance/dlp.py` — payload redaction before audit hashing.

## Consumes
`core` (contracts, tenancy, events) and the `CreditLedger` contract. Never calls
business packages.

## Non-goals
Model calls, tool execution, UI. The rail decides; it does not act.

## Honest gaps (2026-09-15)
- Policy engine is structural, not OPA/Rego (stored for later).
- Cost recorder and rate limiter are in-process (single-node MVP).
- Universal policy-before-execution is not yet complete for admin
  financial/irreversible effects; several mutating endpoints still lack
  idempotency (escalation, invoice draft, spend-cap update, kill-switch/release).

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
