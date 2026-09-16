# api

**Status:** skeleton — interfaces and boundaries only. No business logic yet.

## Boundary
HTTP surface only: FastAPI routers, request/response schemas (Pydantic v2), dependencies (auth, tenancy, pagination, idempotency). NO business logic — routers call service interfaces from domain packages. Contract spec: `API.md`.

## Owned tables
(none — API owns no tables)

## Public interfaces
Routers: `auth`, `tenants`, `crm`, `tasks`, `workflows`, `agents`, `tools`, `approvals`, `budgets`, `audit`, `command_center`, `billing`. `api/dependencies.py`: `get_tenant`, `require_role`, pagination, idempotency key.

## Consumes
Service interfaces from every domain package; `core` only for cross-cutting.

## Non-goals
Business logic of any kind. If a router file grows logic, it is a bug.

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
