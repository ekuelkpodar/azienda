# cli

**Status:** skeleton — interfaces and boundaries only. No business logic yet.

## Boundary
Operational CLI (Typer): tenant provisioning, migrations, reindex, ledger verify, budget controls, seed data. For operators, not end users.

## Owned tables
(none)

## Public interfaces
`azienda` command group; commands call the same service interfaces as the API.

## Consumes
`core` + service interfaces. Never bypasses the governance rail.

## Non-goals
End-user features; anything the API doesn't already expose as a service call.

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
