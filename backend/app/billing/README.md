# billing

**Status:** implemented — the SaaS's OWN billing (not customer accounting).
Verified 2026-09-15: 269 backend tests pass, `ruff check` and `mypy --strict`
clean, Alembic 0001→0003 upgrades clean on SQLite with zero model/migration
column diffs.

## Boundary
Plans, subscriptions, execution credits, usage metering, invoices, spend caps.
**Pricing is DATA** (seeded via CLI `billing-seed-plans`, configurable dimensions),
never hardcoded business logic. Kill switches and spend caps are P0 — unbounded
agent cost is the #1 business risk.

## Owned tables
`billing_plans`, `plan_dimensions`, `subscriptions`, `credit_pools`,
`credit_transactions`, `usage_meters`, `billing_invoices`, `spend_caps`.

## Public interfaces
- `billing/service.py` — `BillingService`: subscribe/change plan, meter usage,
  `draw()` credits (implements the `CreditLedger` contract — the legal
  governance→billing seam, ADR-009), draft invoices, spend caps.
- `billing/plans.py` — plan seeding from data (ASSUMED development pricing;
  tune before any real charge).
- Every credit-affecting row carries `tenant_id` (`credit_pools`,
  `credit_transactions` included).

## Consumes
`core` (contracts, tenancy, events). Drawn upon by governance's cost recorder
through `CreditLedger` only — never the reverse.

## Non-goals
Customer bookkeeping (see `finance/` + NEXORA ERP). Payment collection, external
billing provider integration, and proration are CONFIRMED not implemented.

## Honest gaps (2026-09-15)
- No payment provider (Stripe etc.), no proration, no dunning.
- Seed pricing is ASSUMED dev pricing — must be replaced with real commercial
  figures before any customer charge.
- Invoice draft endpoint lacks idempotency; spend-cap update lacks idempotency.

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
