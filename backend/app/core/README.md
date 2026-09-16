# core

**Status:** implemented — foundation, auth, tenancy, and the cross-package contract seam.
Verified 2026-09-15: 269 backend tests pass (70 owned), `ruff check` clean,
`mypy --strict` clean on all 43 foundation source files, Alembic 0001→0003
upgrades clean on SQLite with zero model/migration column diffs.

## Boundary
Shared kernel: configuration, DB session factory, security primitives, tenancy
helpers, the in-process event bus, idempotency, rate limiting, and ALL
cross-package contracts (`contracts.py`). Every package may depend on `core`;
`core` depends on nothing in the app.

## Owned tables
`tenants`, `users`, `roles`, `user_roles`, `api_keys`, `refresh_tokens`,
`idempotency_keys`.

## Public interfaces
- `core/contracts.py` — the only legal cross-package seam: `PolicyEngine`,
  `RiskScorer`, `ApprovalStore`, `AuditLedger`, `BudgetEnforcer`, `CostRecorder`,
  `CreditLedger`, plus `BudgetView`/`CostLedgerView`/`SpendAlertView` read views
  and `Approval`/`AuditEntry` (now carrying `risk_score`/`risk_factors`/`reasons`
  and `seq`). See ADR-009 for the 2026-09-15 read/admin widening.
- `core/config.py` — `Settings` (`AZIENDA_` prefix; see root `.env.example`).
- `core/auth.py` — `AuthService`: bcrypt passwords, JWT access tokens, rotating
  refresh tokens with reuse detection (reuse → whole chain revoked), API keys.
- `core/tenancy.py` — `Principal`, `TenantContext`; every row carries `tenant_id`.
- `core/security.py` — RBAC role/permission checks; `core/ratelimit.py` —
  in-process sliding-window limiter (CONFIRMED single-process MVP; Redis is future).
- `core/idempotency.py` — idempotency-key storage/replay (`Idempotency-Key` header).
- `core/db.py` — async engine/session; sets Postgres RLS vars per tenant session.
- `core/events.py` — in-process event bus (CONFIRMED MVP; Postgres ledger is the
  durable truth, the bus is transport).
- `core/time.py` — `as_utc()`/`utcnow()` (SQLite returns naive datetimes).

## Consumes
nothing

## Non-goals
Domain logic, HTTP routing, agent loops, policy rules — those live in their own packages.

## Honest gaps (2026-09-15)
- OIDC/SSO, MFA: not implemented (interfaces reserved).
- Redis/Valkey rate limiting: future; current limiter is per-process.
- Full OTel decision provenance: partial.
- Postgres RLS migration SQL is implemented and statically verified; runtime RLS
  behavior is UNKNOWN in this environment (no Postgres available).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols (or this package's
   own public interface). No cross-package private imports. The single documented
   exception is the composition root (`app/api/deps.py` + operator CLI), which binds
   implementations to protocols in one auditable place (ADR-009).
2. Every row this package writes carries `tenant_id`. Every query filters by it. Add a
   cross-tenant invisibility test for every new table.
3. Mutations that matter emit AGRL/domain events via the `EventBus`; the Postgres ledger
   is truth, the bus is transport.
4. Anything irreversible, financial, bulk, or externally visible goes through the
   governance rail (`governance/`) BEFORE execution. Fail closed.
5. No secrets in code or logs. Config comes from env.
