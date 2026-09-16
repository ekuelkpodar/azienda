# SECURITY.md — Azienda security posture

> **Evidence labels:** CONFIRMED = verified in this repo's code or test output
> on 2026-09-16. LIKELY/ASSUMED = design intent not yet verified. UNKNOWN =
> not yet assessed. Claims without a label are description, not fact.

## What is actually enforced today (CONFIRMED)

1. **Authentication** — JWT access tokens (HS256, `backend/app/auth/`), refresh
   rotation with reuse detection, API keys, tenant-scoped foundation
   dependencies. 16 auth/tenancy tests pass.
2. **Tenant isolation** — Every business-app service filters by tenant. Postgres
   adds defense-in-depth RLS policies (static checks in migration tests;
   **live-Postgres enforcement is UNKNOWN — not tested against a live
   instance**). Manual cross-tenant ASGI probes: 7/7 pass (invisible reads,
   401 on missing tenant, 403 on policy deny).
3. **Policy-before-execution** — `core/contracts.py` is the only cross-package
   seam. Writes are gated by the policy engine; in production the engine runs
   a deliberately permissive stance that **refuses production use** — it is a
   stub with a fail-closed guard, not a production decision point.
4. **Audit ledger** — Hash-chained, append-only audit records (26 governance
   tests pass, including chain verification).
5. **Approvals** — Human-approval workflow for risk-gated actions with
   timeout==DENY (expired approvals deny by default).
6. **Budgets / kill switch** — Per-tenant spend budgets and a global kill
   switch that freezes agent execution.
7. **Input validation** — Pydantic v2 schemas on all request/response models;
   FastAPI dependency injection on all foundation routers.

## Known gaps (honest — do not claim otherwise)

| Gap | Label | Impact |
|---|---|---|
| Business-app routers (CRM, tasks, workflows, comms, marketing, support, scheduling, finance) use `app/api/routers/_common.py` with client-supplied `X-Tenant-Id` instead of JWT-derived tenant | CONFIRMED | Tenant spoofing possible **except** a fail-closed guard returns 401 when `AZIENDA_ENVIRONMENT=production`. Must be refactored to JWT before any production deploy. |
| Rate limiting is process-local; Redis/Valkey limiter not implemented | CONFIRMED | No distributed rate limiting across replicas. |
| Full OPA/Rego evaluation not implemented; rule engine is limited | CONFIRMED | Complex policies cannot be expressed yet. |
| OIDC/SSO and MFA not implemented | CONFIRMED | Only password + JWT + API keys. |
| AGRL and knowledge stores are in-memory adapters despite migration tables | CONFIRMED | State does not survive restart. |
| Live Postgres RLS not tested; pgvector behavior not verified | CONFIRMED gap | Migration chain verified on SQLite only. |
| Live LLM providers, billing providers, comms providers not integrated | CONFIRMED | No external credential handling needed yet. |

## Reporting vulnerabilities

This is an MVP research codebase (2026-09-16). Do not deploy it to production.
If you find a security issue, contact the maintainer before publishing it.
