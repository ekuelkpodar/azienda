# ROADMAP.md — Azienda

> Honest sequenced plan. "Done" = verified 2026-09-16. "Next" = ordered by
> what unblocks production and revenue.

## Done (MVP wave, 2026-09-15/16)

- Modular FastAPI monolith: 15 packages behind `core/contracts.py`.
- 269 backend tests green; 18 frontend tests green; frontend prod build green.
- Alembic linear chain (6 revisions), SQLite round-trip verified (91 tables).
- JWT auth + refresh rotation + RBAC + API keys; policy engine, approvals,
  hash-chained audit, budgets + kill switch, billing models, admin endpoints.
- 3 deterministic signature demos (12 + 9 + 11 steps, all PASS).
- Research, architecture, API, database, security, threat-model, eval docs.

## Next — production hardening (in order)

1. **JWT tenancy on all routers** — replace `_common.py` `X-Tenant-Id` seam on
   8 business-app routers; delete the header seam. (Top security blocker.)
2. **Live Postgres + pgvector + RLS** — boot compose, apply migrations,
   test RLS enforcement for real.
3. **Persistent AGRL + knowledge stores** — replace in-memory adapters.
4. **Redis/Valkey** — distributed rate limiting, locks, ARQ worker.
5. **OPA/Rego** — full policy evaluation replacing the limited rule engine.
6. **Wire agent service graph** into the production app factory; wire
   command-center and tools routers.
7. **OIDC/SSO + MFA.**
8. **Secrets management** (Vault or equivalent).

## Then — revenue

9. **Billing provider integration** (Stripe or equivalent) + proration.
10. **Comms providers** (SMS/email/voice) out of `log_only` mode.
11. **LiteLLM provider abstraction** — live model routing with cost metering.
12. **MCP live round-trips** against real servers.
13. **Eval suites** — rail red-team, tool-call accuracy, cost regression,
    recovery (see EVALUATION.md).
14. **Pricing launch** — implement PRICING.md dimensions in `billing/`.

## Explicitly out of scope (no ADR, no step-up trigger)

Kafka, Kubernetes, a dedicated vector DB, microservices split
(ARCHITECTURE.md §12).
